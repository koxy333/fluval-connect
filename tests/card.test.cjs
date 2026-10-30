const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('custom_components/fluval_connect/frontend/fluval-connect-card.js', 'utf8');
function fixture() {
  const calls=[];
  class HTMLElement { attachShadow(){this.shadowRoot={innerHTML:'',activeElement:null};} }
  const ctx=vm.createContext({HTMLElement, customElements:{get:()=>false,define:()=>{}}, window:{}});
  vm.runInContext(source+'\nglobalThis.Card=FluvalConnectCard;',ctx);
  const card=new ctx.Card(); card._bind=()=>{};
  card.setConfig({name:'Test aquarium',device_id:'test-device'});
  const states={};
  function add(domain,label,state,attributes={}) {
    const id=domain+'.test_'+Object.keys(states).length;
    states[id]={entity_id:id,state,attributes:{friendly_name:'Test aquarium'+(label?' '+label:''),...attributes}};
    return id;
  }
  add('light','','on',{brightness:255}); add('select','Mode','Auto');
  add('button','Refresh status','unknown'); add('number','Manual red','40');
  add('time','Sunrise start','07:00:00'); add('select','Weather effect','None',{options:['None','Weather effect 1']});
  add('sensor','Pro schedule','2 points',{points:[{time:'08:00',red:0,green:0,blue:0,white:0},{time:'20:00',red:10,green:20,blue:30,white:40}]});
  card.hass={states,entities:{},callService:async(...args)=>calls.push(args)};
  return {card,calls};
}
test('rendering shows live values and never sends commands',()=>{
  const {card,calls}=fixture();
  assert.match(card.shadowRoot.innerHTML,/Connected to Fluval/);
  assert.match(card.shadowRoot.innerHTML,/value="07:00"/);
  assert.equal(calls.length,0);
});
test('power, mode, colour, time and refresh dispatch to the chosen entities',async()=>{
  const {card,calls}=fixture();
  await card._action('off'); await card._set('select','Mode','select_option',{option:'Pro'});
  await card._set('number','Manual red','set_value',{value:75});
  await card._set('time','Sunrise start','set_value',{time:'08:00:00'});
  await card._action('refresh');
  assert.deepEqual(calls.map(c=>c.slice(0,2)),[['light','turn_off'],['select','select_option'],['number','set_value'],['time','set_value'],['button','press']]);
  assert.equal(calls[2][2].value,75);
  assert.equal(card._message,'Status refresh requested');
});
test('Pro draft cancellation sends nothing; valid save sends one whole timeline',async()=>{
  const {card,calls}=fixture();
  await card._action('edit-pro'); card._proDraft[0].red=55;
  await card._action('cancel-pro'); assert.equal(calls.length,0);
  assert.equal(card._entity('sensor','Pro schedule').attributes.points[0].red,0);
  await card._action('edit-pro'); await card._action('save-pro');
  assert.equal(calls.length,1); assert.equal(calls[0][1],'set_pro_schedule');
  assert.equal(calls[0][2].device_id,'test-device'); assert.equal(calls[0][2].points.length,2);
});
test('invalid Pro order is rejected without a command and errors are visible',async()=>{
  const {card,calls}=fixture();
  await card._action('edit-pro'); card._proDraft[1].time='07:00';
  await card._action('save-pro'); assert.equal(calls.length,0);
  assert.match(card._message,/ordered times/);
  await card._run('Test',async()=>{throw Error('Test unavailable');});
  assert.match(card.shadowRoot.innerHTML,/Test unavailable/);
});
