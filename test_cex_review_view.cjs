const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=fs.readFileSync('cex-review.js','utf8');new vm.Script(script);
const category=vm.runInNewContext('('+script.match(/function crossCategory\(r,now=[\s\S]*?\n\}/)[0]+')');
assert.equal(category({valid_until:110,identity_verified:true,amount_checks_passed:true},100),'金额检查通过，完整套利未验证');
assert.equal(category({valid_until:90,identity_verified:true,amount_checks_passed:true},100),'过期');
assert.equal(category({valid_until:110,identity_verified:false,conditional_net_usdt:'700'},100),'身份待核实');
assert.equal(category({valid_until:110,identity_verified:true,amount_checks_passed:false},100),'有阻碍 / 待核实');
assert.ok(!script.includes('.innerHTML'));
assert.ok(script.includes('crossEpoch'));
console.log('cross-venue UI syntax, expiry and unverified status checks passed');

