"""Durable flag submission with explicit platform outcomes."""
import hashlib
import json
import time
from .core import protect

def classify(response):
    if response.status_code==429:return 'rate_limited'
    try:data=response.json()
    except Exception:data={}
    if isinstance(data,dict):
        status=str(data.get('status',data.get('result',''))).lower()
        if data.get('accepted') is True or status in ('accepted','success','ok'):return 'accepted'
        if status in ('duplicate','already_submitted'):return 'duplicate'
        if data.get('accepted') is False or status in ('rejected','invalid','error','failed'):return 'rejected'
    text=response.text.strip().lower()
    if text in ('accepted','success','ok','flag accepted'):return 'accepted'
    if text in ('duplicate','already submitted'):return 'duplicate'
    if 400<=response.status_code<500:return 'rejected'
    return 'unknown'

class SubmissionAdapter:
    def __init__(self,store):
        self.store=store
        with store.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY,competition TEXT,target TEXT,endpoint TEXT,status TEXT,updated REAL,body BLOB,task TEXT)')
    def submit(self,flag,target,url,token,session,competition,task):
        identity=hashlib.sha256((competition+'\0'+url+'\0'+flag).encode()).hexdigest()
        with self.store.connect() as c:
            prior=c.execute('SELECT status FROM submissions WHERE id=?',(identity,)).fetchone()
            if prior and prior['status'] in ('accepted','duplicate','sending','unknown'):return {'id':identity,'status':prior['status'],'cached':True}
            body=protect(json.dumps({'flag':flag,'token':token},ensure_ascii=False).encode())
            c.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated,task=excluded.task',(identity,competition,target,url,'sending',time.time(),body,task))
        try:
            response=session.post(url,data={'flag':flag,'token':token},timeout=10);status=classify(response);code=response.status_code
        except Exception:status='unknown';code=None
        with self.store.connect() as c:c.execute('UPDATE submissions SET status=?,updated=? WHERE id=?',(status,time.time(),identity))
        return {'id':identity,'status':status,'http_status':code}

def install(module,env):
    from .core import Store
    queue=SubmissionAdapter(Store(env.request['root'],env.request['data_dir']))
    def submit(actor,flag,ip):
        if flag in actor.known:return False
        urls=[u.strip() for u in actor.entry_submit.get().splitlines() if u.strip()]
        if actor.no_submit_var.get() or not urls:
            env.emit('flag_extracted',{'target':ip,'flag':flag,'submission':'disabled'});actor.known.add(flag);return True
        accepted=False
        for url in urls:
            env.check_url(url)
            outcome=queue.submit(flag,ip,url,actor.entry_token.get().strip(),module._session(),env.request['context']['competition'],env.request['context']['task_id'])
            env.emit('submission',outcome);status=outcome['status']
            actor.log_self('平台提交结果：'+status,'ok' if status in ('accepted','duplicate') else 'fail')
            if status in ('accepted','duplicate'):accepted=True
        if accepted:
            actor.known.add(flag);actor.total_submitted+=1;actor.save_known()
        return accepted
    module.FlagTab.submit_flag=submit;module.FlagTab.submit_flag_multi=submit
    def manual(actor):
        flag=actor.manual_flag.get().strip()
        if not flag:raise ValueError('manual_flag 必填')
        return submit(actor,flag,actor.manual_ip.get().strip() or 'manual')
    module.FlagTab.manual_submit=manual
