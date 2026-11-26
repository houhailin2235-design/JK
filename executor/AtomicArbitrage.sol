// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IV2Router {
    function swapExactTokensForTokens(uint256,uint256,address[] calldata,address,uint256)
        external returns (uint256[] memory);
}

/// @notice Owner-confirmed, pre-funded, same-chain V2 round trip. No flash loans.
/// @dev Gross token profit is enforced. The caller must budget gas separately.
contract AtomicArbitrage is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    IERC20 public immutable asset;
    address public immutable routerA;
    address public immutable routerB;
    uint256 public immutable maxInput;

    struct Order {
        address buyRouter;
        address sellRouter;
        address[] buyPath;
        address[] sellPath;
        uint256 amountIn;
        uint256 minBuyOut;
        uint256 minGrossProfit;
        uint256 maxGasPrice;
        uint256 deadline;
    }
    event Executed(address indexed owner,uint256 input,uint256 grossProfit);

    constructor(address owner_,address asset_,address first,address second,uint256 cap) Ownable(owner_) {
        require(asset_.code.length>0 && first.code.length>0 && second.code.length>0,"NO_CODE");
        require(first!=second && cap>0,"BAD_CONFIG");
        asset=IERC20(asset_);routerA=first;routerB=second;maxInput=cap;
    }

    function execute(Order calldata o) external onlyOwner whenNotPaused nonReentrant returns (uint256 profit) {
        require(o.amountIn>0 && o.amountIn<=maxInput && o.minGrossProfit>0 && o.minBuyOut>0,"LIMIT");
        require(tx.gasprice<=o.maxGasPrice && block.timestamp<=o.deadline && o.deadline<=block.timestamp+120,"EXPIRED_OR_GAS");
        require((o.buyRouter==routerA && o.sellRouter==routerB) || (o.buyRouter==routerB && o.sellRouter==routerA),"ROUTER");
        require(o.buyPath.length>=2 && o.buyPath.length<=3 && o.sellPath.length>=2 && o.sellPath.length<=3,"PATH");
        address middle=o.buyPath[o.buyPath.length-1];
        require(o.buyPath[0]==address(asset) && middle!=address(asset) && o.sellPath[0]==middle && o.sellPath[o.sellPath.length-1]==address(asset),"ASSET");
        uint256 baseline=asset.balanceOf(address(this));
        uint256 middleBefore=IERC20(middle).balanceOf(address(this));
        asset.safeTransferFrom(msg.sender,address(this),o.amountIn);
        require(asset.balanceOf(address(this))==baseline+o.amountIn,"INPUT_TAX");
        asset.forceApprove(o.buyRouter,o.amountIn);
        IV2Router(o.buyRouter).swapExactTokensForTokens(o.amountIn,o.minBuyOut,o.buyPath,address(this),o.deadline);
        asset.forceApprove(o.buyRouter,0);
        uint256 received=IERC20(middle).balanceOf(address(this))-middleBefore;
        require(received>=o.minBuyOut,"BUY_OUTPUT");
        IERC20(middle).forceApprove(o.sellRouter,received);
        IV2Router(o.sellRouter).swapExactTokensForTokens(received,o.amountIn+o.minGrossProfit,o.sellPath,address(this),o.deadline);
        IERC20(middle).forceApprove(o.sellRouter,0);
        uint256 ending=asset.balanceOf(address(this));
        require(ending>=baseline+o.amountIn+o.minGrossProfit,"NO_PROFIT");
        profit=ending-baseline-o.amountIn;
        asset.safeTransfer(msg.sender,ending-baseline);
        require(asset.balanceOf(address(this))>=baseline,"BASELINE");
        emit Executed(msg.sender,o.amountIn,profit);
    }

    function setPaused(bool value) external onlyOwner {if(value)_pause();else _unpause();}
    function recover(IERC20 token,uint256 amount) external onlyOwner nonReentrant {token.safeTransfer(owner(),amount);}
}
