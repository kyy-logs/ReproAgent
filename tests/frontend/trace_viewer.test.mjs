import test from 'node:test';
import assert from 'node:assert/strict';
import {computeVisibleRows, formatStartTime} from '../../src/reproagent/resources/trace_viewer.mjs';

function fixture() {
 return {roots:[0],nodes:[
 {key:0,label:'task',kind_tag:'ENTRY',parent_key:null,child_keys:[1],content_keys:[],attributes:{}},
 {key:1,label:'sdk.model_round',kind_tag:'LLM',parent_key:0,child_keys:[2,3],content_keys:[],attributes:{},display_status:'ok',otel_status:'ERROR'},
 {key:2,label:'tool.Read',kind_tag:'TOOL',parent_key:1,child_keys:[],content_keys:[0],attributes:{},display_status:'ok'},
 {key:3,label:'tool.Read',kind_tag:'TOOL',parent_key:1,child_keys:[],content_keys:[0],attributes:{result_code:'FAIL'},display_status:'failed'}]};
}
function state(extra={}) { return {query:'',typeSet:new Set(),errorsOnly:false,collapsedKeys:new Set(),...extra}; }
const keys=rows=>rows.map(n=>n.key);

test('matching_descendant_preserves_and_opens_ancestors',()=>{
 const s=state({query:'tool.Read',collapsedKeys:new Set([0,1])});
 assert.deepEqual(keys(computeVisibleRows(fixture(),s,new Set())),[0,1,2,3]);
 assert.deepEqual([...s.collapsedKeys],[0,1]);
});
test('clearing_query_restores_manual_collapses',()=>{
 const s=state({query:'tool.Read',collapsedKeys:new Set([1])});
 assert.equal(computeVisibleRows(fixture(),s,new Set()).length,4);
 s.query=''; assert.deepEqual(keys(computeVisibleRows(fixture(),s,new Set())),[0,1]);
});
test('combined_filters_distinguish_expected_otel_error',()=>{
 assert.deepEqual(keys(computeVisibleRows(fixture(),state({typeSet:new Set(['TOOL']),errorsOnly:true}),new Set())),[0,1,3]);
 assert.deepEqual(keys(computeVisibleRows(fixture(),state({typeSet:new Set(['LLM']),errorsOnly:true}),new Set())),[]);
});
test('shared_content_matches_without_per_node_body_duplication',()=>{
 const view=fixture();
 assert.deepEqual(keys(computeVisibleRows(view,state({query:'body-only'}),new Set([0]))),[0,1,2,3]);
 assert.equal(JSON.stringify(view).includes('body-only'),false);
 assert.deepEqual(keys(computeVisibleRows(view,state({query:'missing'}),new Set())),[]);
});
test('orphan_and_1024_deep_tree_are_iterative',()=>{
 const nodes=Array.from({length:1024},(_,i)=>({key:i,label:'step',kind_tag:'OTHER',parent_key:i?i-1:null,child_keys:i<1023?[i+1]:[],content_keys:[],attributes:{}}));
 assert.equal(computeVisibleRows({nodes,roots:[0]},state(),new Set()).length,1024);
 nodes[1023].label='target';
 assert.equal(computeVisibleRows({nodes,roots:[0]},state({query:'target',collapsedKeys:new Set([0])}),new Set()).length,1024);
 assert.deepEqual(keys(computeVisibleRows({nodes:[{...nodes[0],child_keys:[],missing_parent:true}],roots:[0]},state(),new Set())),[0]);
});
test('permission_results_can_be_filtered_separately',()=>{
 const view=fixture(); view.nodes[2].permission_result='DENIED'; view.nodes[3].permission_result='RESERVED_FOR_PUBLISHING';
 assert.deepEqual(keys(computeVisibleRows(view,state({permissionFilter:'DENIED'}),new Set())),[0,1,2]);
 assert.deepEqual(keys(computeVisibleRows(view,state({permissionFilter:'RESERVED_FOR_PUBLISHING'}),new Set())),[0,1,3]);
});

test('native_unix_seconds_render_in_the_current_century',()=>{
 assert.match(formatStartTime(1791583200), /2026/);
 assert.match(formatStartTime('2026-10-10T00:00:00Z'), /2026/);
 assert.equal(formatStartTime(null),'未记录');
});
