const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8');
for(const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
const category=vm.runInNewContext('('+html.match(/function arbCategory\(p\)\{[^\n]+/)[0]+')');
assert.equal(category({stale:false,positive_quote:true,route_quality_passed:true}),'candidate');
assert.equal(category({stale:true,positive_quote:true,route_quality_passed:true}),'stale');
assert.equal(category({stale:false,positive_quote:false,route_quality_passed:false}),'rejected');
assert.equal(category({stale:false,positive_quote:true}),'rejected','legacy unchecked positives must fail closed');
console.log('arb UI script syntax and candidate/failed/stale separation passed');
