// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
contract TestToken is ERC20 {
    constructor() ERC20("Test","TEST") {}
    function mint(address to,uint256 amount) external {_mint(to,amount);}
}
contract TestRouter {
    uint256 public rate=10000;
    function setRate(uint256 value) external {rate=value;}
    function swapExactTokensForTokens(uint256 amount,uint256 minimum,address[] calldata path,address to,uint256)
        external returns(uint256[] memory result) {
        require(IERC20(path[0]).transferFrom(msg.sender,address(this),amount),"TRANSFER");
        uint256 out=amount*rate/10000;require(out>=minimum,"SLIPPAGE");
        require(IERC20(path[path.length-1]).transfer(to,out),"OUTPUT");
        result=new uint256[](path.length);result[0]=amount;result[path.length-1]=out;
    }
}
