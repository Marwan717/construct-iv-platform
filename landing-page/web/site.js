const menu=document.querySelector('.menu'),nav=document.querySelector('.nav-links');menu?.addEventListener('click',()=>{const open=menu.getAttribute('aria-expanded')!=='true';menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'Close menu':'Open menu');nav.classList.toggle('open',open)});const form=document.getElementById('contact-form'),status=document.getElementById('form-status');if(form){const preset=new URLSearchParams(location.search).get('inquiry');if(preset&&[...form.inquiryType.options].some(o=>o.value===preset))form.inquiryType.value=preset;form.addEventListener('submit',async e=>{e.preventDefault();if(!form.reportValidity())return;const button=form.querySelector('button[type=submit]');button.disabled=true;button.textContent='Saving…';status.className='form-status';status.textContent='';try{const values=Object.fromEntries(new FormData(form));const response=await fetch('/api/contact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const result=await response.json();if(!response.ok)throw new Error(result.error||'Your inquiry could not be saved. Please try again.');status.classList.add('success');status.textContent='Thank you. Your inquiry has been saved for review. Reference: '+result.id;form.reset()}catch(error){status.classList.add('error');status.textContent=error.message||'Unable to save your inquiry. Please try again.'}finally{button.disabled=false;button.textContent='Submit ↗'}})}
const teamSize=document.getElementById('team-size');
if(teamSize){
 let billing='monthly';
 const summary=document.getElementById('team-summary'),teamError=document.getElementById('team-error'),demo=document.getElementById('pricing-demo'),enterpriseURL=demo.dataset.enterpriseUrl||demo.href;
 function updatePricing(){
  const raw=teamSize.value.trim(),size=Number(raw),valid=raw!==''&&teamSize.validity.valid&&Number.isInteger(size)&&size>=1&&size<=100000;
  const invalid=raw!==''&&!valid;
  teamSize.setAttribute('aria-invalid',String(invalid));teamError.hidden=!invalid;
  document.getElementById('price-period').textContent=billing==='annual'?'Your annual price':'Your monthly price';
  summary.textContent=valid?`Request ${billing} pricing for ${size.toLocaleString()} team ${size===1?'member':'members'}.`:invalid?'Check your team size to continue.':'Enter your team size to get started.';
  const params=new URLSearchParams();
  if(valid){params.set('team',String(size));params.set('billing',billing)}
  demo.href=enterpriseURL+(params.size?'?'+params.toString():'');
 }
 teamSize.addEventListener('input',updatePricing);
 document.querySelectorAll('[data-billing]').forEach(button=>button.addEventListener('click',()=>{billing=button.dataset.billing;document.querySelectorAll('[data-billing]').forEach(other=>{const selected=other===button;other.classList.toggle('selected',selected);other.setAttribute('aria-pressed',String(selected))});updatePricing()}));
 updatePricing();
}
if(form){const params=new URLSearchParams(location.search),size=Number(params.get('team')),billing=params.get('billing');if(Number.isInteger(size)&&size>0&&size<=100000&&['monthly','annual'].includes(billing))form.message.value=`I’d like a demo and ${billing} pricing for a team of ${size.toLocaleString()}.\n\nProject details: `;}
document.querySelectorAll('[data-carousel]').forEach(carousel=>{
 const track=carousel.querySelector('[data-carousel-track]'),prev=carousel.querySelector('[data-carousel-prev]'),next=carousel.querySelector('[data-carousel-next]'),count=carousel.querySelector('.carousel-count');
 if(!track||!prev||!next)return;
 const cards=[...track.children];
 const update=()=>{const step=cards[0]?.getBoundingClientRect().width+(parseFloat(getComputedStyle(track).columnGap)||0)||1;const index=Math.min(cards.length,Math.max(1,Math.round(track.scrollLeft/step)+1));if(count)count.innerHTML=String(index).padStart(2,'0')+' <i>/ '+String(cards.length).padStart(2,'0')+'</i>';prev.disabled=track.scrollLeft<4;next.disabled=track.scrollLeft+track.clientWidth>=track.scrollWidth-4};
 prev.addEventListener('click',()=>track.scrollBy({left:-track.clientWidth*.82,behavior:'smooth'}));
 next.addEventListener('click',()=>track.scrollBy({left:track.clientWidth*.82,behavior:'smooth'}));
 track.addEventListener('scroll',update,{passive:true});
 window.addEventListener('resize',update,{passive:true});
 update();
});
