export function pilotDestination(identity, search='') {
  if(identity.role==='worker')return '/worker';
  if(identity.role!=='owner')throw Error('Роль учётной записи не поддерживается.');
  const value=new URLSearchParams(search).get('return')||'';
  // A local R3 fragment only. Never use arbitrary redirects from the URL.
  const fragment=/^#[a-z][a-z0-9_\-/?.=&%:]*$/i.test(value)&&value.length<1000?value:'#home';
  return '/r3/connected.html'+fragment;
}
if(typeof document!=='undefined')document.querySelector('#pilot-login').addEventListener('submit',async event=>{
 event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),error=document.querySelector('#login-error');button.disabled=true;error.textContent='';
 try{const response=await fetch('/pilot/login',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.username.value.trim(),password:form.password.value})});
 if(!response.ok){error.textContent=response.status===429?'Слишком много попыток. Подождите и повторите.':response.status===410?'Срок тестового доступа закончился.':'Не удалось войти. Проверьте имя и пароль.';form.password.value='';return;}
 const identity=await response.json();form.password.value='';if(typeof BroadcastChannel==='function'){const channel=new BroadcastChannel('greenhouse-pilot-session');channel.postMessage({changed:true});channel.close();}location.replace(pilotDestination(identity,location.search));
 }catch{error.textContent='Не удалось подключиться. Введённое имя сохранено. Повторите вход.';}finally{button.disabled=false;}
});
