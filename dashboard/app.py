"""Local-only lead dashboard. One process / one sequential durable worker."""
import csv
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from contextlib import contextmanager
from flask import Flask, jsonify, render_template, request, send_file, session, abort
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent
STATUSES = {'new', 'contacted', 'interested', 'followup', 'won', 'closed'}
FIELDS = ['name','phone','email','website','address','source_url','instagram','facebook','linkedin','country','city','category','notes','tags','status']

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()

def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY,name TEXT,country TEXT,category TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,project_id INTEGER REFERENCES projects(id),city TEXT,keyword TEXT,depth INTEGER,email INTEGER,socials INTEGER,state TEXT DEFAULT 'queued',engine_id TEXT,error TEXT DEFAULT '',count INTEGER DEFAULT 0,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY,name TEXT,phone TEXT DEFAULT '',email TEXT DEFAULT '',website TEXT DEFAULT '',address TEXT DEFAULT '',source_url TEXT DEFAULT '',instagram TEXT DEFAULT '',facebook TEXT DEFAULT '',linkedin TEXT DEFAULT '',country TEXT,city TEXT,category TEXT,notes TEXT DEFAULT '',tags TEXT DEFAULT '',status TEXT DEFAULT 'new',created TEXT);
        CREATE TABLE IF NOT EXISTS identities(identity TEXT PRIMARY KEY,lead_id INTEGER REFERENCES leads(id));
        CREATE TABLE IF NOT EXISTS project_leads(project_id INTEGER REFERENCES projects(id),lead_id INTEGER REFERENCES leads(id),PRIMARY KEY(project_id,lead_id));
        CREATE TABLE IF NOT EXISTS geocache(query TEXT PRIMARY KEY,lat TEXT,lon TEXT);
        ''')

def identities(row):
    keys = [f'{key}:{row[key].strip()}' for key in ('place_id','cid','link') if row.get(key,'').strip()]
    if not keys and row.get('title') and row.get('address'):
        value='|'.join(row.get(k,'').strip().casefold() for k in ('title','address'))
        keys=['address:'+hashlib.sha256(value.encode()).hexdigest()]
    return keys

def ingest(db, rows, project, city):
    count=0
    for row in rows:
        if not row.get('title','').strip():
            continue
        keys=identities(row)
        lead=None
        for key in keys:
            found=db.execute('SELECT lead_id FROM identities WHERE identity=?',(key,)).fetchone()
            if found:
                lead=found[0]; break
        if lead is None:
            values=[row.get(k,'') for k in ('title','phone','emails','website','address','link','instagram','facebook','linkedin')]
            lead=db.execute('INSERT INTO leads(name,phone,email,website,address,source_url,instagram,facebook,linkedin,country,city,category,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',values+[project['country'],city,row.get('category') or project['category'],now()]).lastrowid
        for key in keys:
            db.execute('INSERT OR IGNORE INTO identities VALUES (?,?)',(key,lead))
        db.execute('INSERT OR IGNORE INTO project_leads VALUES (?,?)',(project['id'],lead))
        count+=1
    return count

class Engine:
    def __init__(self, base): self.base=base.rstrip('/')
    def call(self, method, path, body=None):
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(self.base+path,data=data,method=method,headers={'Content-Type':'application/json','User-Agent':'AzarDandehLocalDashboard/1.0'})
        with urllib.request.urlopen(req,timeout=30) as r: return r.read()
    def geocode(self, place):
        url='https://nominatim.openstreetmap.org/search?'+urllib.parse.urlencode({'q':place,'format':'json','limit':1})
        req=urllib.request.Request(url,headers={'User-Agent':'AzarDandehLocalDashboard/1.0 (local Google Maps Scraper Kit)'})
        try:
            with urllib.request.urlopen(req,timeout=25) as r: hits=json.load(r)
        finally: time.sleep(1.1)
        if not hits: raise ValueError('شهر پیدا نشد؛ نام شهر و کشور را دقیق‌تر وارد کنید.')
        return str(hits[0]['lat']),str(hits[0]['lon'])

def safe_public_url(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,80,443):
        raise ValueError('نشانی وب مجاز نیست')
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('نشانی شبکهٔ خصوصی مجاز نیست')
    return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def social_profiles(url):
    # Never follow redirects to private networks; profile pages themselves are not scraped.
    from scripts.scrape import _find_socials
    safe_public_url(url)
    req=urllib.request.Request(url,headers={'User-Agent':'AzarDandehLocalDashboard/1.0'})
    with urllib.request.build_opener(NoRedirect).open(req,timeout=8) as r:
        return _find_socials(r.read(400000).decode('utf-8','replace'))

def process_one(app):
    path=app.config['DB']; engine=app.config['ENGINE']
    with connect(path) as db:
        job=db.execute("SELECT * FROM jobs WHERE state IN ('working','queued') ORDER BY id LIMIT 1").fetchone()
        if job is None: return False
        job=dict(job); project=dict(db.execute('SELECT * FROM projects WHERE id=?',(job['project_id'],)).fetchone())
        if job['state']=='queued':
            db.execute("UPDATE jobs SET state='working',updated=? WHERE id=?",(now(),job['id']))
    try:
        if job['state']=='working' and (datetime.now(timezone.utc)-datetime.fromisoformat(job['updated'])).total_seconds()>900:
            raise ValueError('زمان انتظار موتور بیش از ۱۵ دقیقه شد؛ گزارش موتور را بررسی کنید. برای جلوگیری از کار تکراری، تلاش خودکار انجام نمی‌شود.')
        if not job['engine_id']:
            place=f"{job['city']}, {project['country']}"
            with connect(path) as db: geo=db.execute('SELECT lat,lon FROM geocache WHERE query=?',(place,)).fetchone()
            if not geo:
                geo=engine.geocode(place)
                with connect(path) as db: db.execute('INSERT OR REPLACE INTO geocache VALUES (?,?,?)',(place,*geo))
            body={'name':f"Azar dashboard {job['id']}",'keywords':[f"{job['keyword']} in {place}"],'lat':geo[0],'lon':geo[1],'lang':'en','zoom':15,'radius':10000,'depth':job['depth'],'email':bool(job['email']),'max_time':300}
            job['engine_id']=json.loads(engine.call('POST','/api/v1/jobs',body))['id']
            with connect(path) as db: db.execute('UPDATE jobs SET engine_id=?,updated=? WHERE id=?',(job['engine_id'],now(),job['id']))
        prefix='/api/v1/jobs/'+urllib.parse.quote(job['engine_id'],safe='')
        state=json.loads(engine.call('GET',prefix)).get('Status')
        if state=='failed': raise ValueError('موتور استخراج خطا داد؛ گزارش موتور را بررسی کنید. محدودیت شبکه یا Google ممکن است علت باشد.')
        if state!='ok':
            if state not in ('pending','working','queued'): raise ValueError('وضعیت ناشناخته از موتور استخراج')
            return True
        raw=engine.call('GET',prefix+'/download').decode('utf-8-sig')
        rows=list(csv.DictReader(io.StringIO(raw)))
        warnings=[]
        if job['socials']:
            for row in rows:
                if row.get('website'):
                    try: row.update(social_profiles(row['website']))
                    except Exception: warnings.append('پروفایل‌های برخی وب‌سایت‌ها در دسترس نبود؛ محدودیت‌ها تغییر داده نشدند.')
        with connect(path) as db:
            count=ingest(db,rows,project,job['city'])
            message='' if count else 'خروجی خالی است؛ نبود نتیجه یا خطای دسترسی موتور محتمل است. وضعیت ok موتور به‌تنهایی موفقیت را ثابت نمی‌کند.'
            db.execute('UPDATE jobs SET state=?,count=?,error=?,updated=? WHERE id=?',('done' if count else 'empty',count,message or (' '.join(set(warnings))),now(),job['id']))
    except Exception as exc:
        app.logger.warning('Job %s failed: %s',job['id'],type(exc).__name__)
        message=str(exc)[:600]
        with connect(path) as db: db.execute("UPDATE jobs SET state='failed',error=?,updated=? WHERE id=?",('اجرای جست‌وجو ناموفق بود: '+message,now(),job['id']))
    return True

def worker(app):
    while True:
        try: process_one(app)
        except Exception: app.logger.exception('Worker iteration failed')
        time.sleep(3)

def create_app(config=None):
    app=Flask(__name__)
    app.config.update(DB=os.environ.get('DASHBOARD_DB','/data/leads.sqlite3'),ENGINE=Engine(os.environ.get('ENGINE_URL','http://google-maps-scraper:8080')),MAX_CONTENT_LENGTH=128*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict')
    if config: app.config.update(config)
    init_db(app.config['DB'])
    keypath=Path(app.config['DB']).parent/'session.key'
    if not keypath.exists():
        try:
            with keypath.open('x') as f: f.write(secrets.token_hex(32))
            keypath.chmod(0o600)
        except FileExistsError: pass
    app.secret_key=keypath.read_text()

    @app.before_request
    def local_only():
        # Host validation also prevents DNS rebinding. Compose publishes only loopback.
        if request.host.split(':')[0].lower() not in ('localhost','127.0.0.1','[::1]'):
            abort(403)
        if request.path.startswith('/api/'):
            if request.headers.get('X-CSRF-Token')!=session.get('csrf') or not session.get('csrf'): abort(403)
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('Origin')
            if origin and origin!=request.host_url.rstrip('/'): abort(403)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def error(exc): return jsonify(error={400:'اطلاعات ورودی معتبر نیست.',403:'درخواست محلی معتبر نیست؛ صفحه را دوباره باز کنید.',404:'مورد پیدا نشد.',413:'حجم درخواست بیش از حد مجاز است.'}[exc.code]),exc.code

    @app.get('/')
    def index():
        session.setdefault('csrf',secrets.token_hex(32))
        return render_template('index.html',csrf=session['csrf'])

    @app.get('/health')
    def health():
        try:
            with connect(app.config['DB']) as db: db.execute('SELECT 1')
            return jsonify(status='ok')
        except sqlite3.Error: return jsonify(status='error'),503

    @app.get('/api/health')
    def api_health():
        try:
            app.config['ENGINE'].call('GET','/api/v1/jobs')
            return jsonify(database='ok',scraper='ok')
        except Exception: return jsonify(database='ok',scraper='unavailable',error='موتور استخراج در دسترس نیست؛ Docker را بررسی کنید.'),503

    @app.get('/api/summary')
    def summary():
        with connect(app.config['DB']) as db:
            return jsonify(leads=db.execute('SELECT count(*) FROM leads').fetchone()[0],countries=db.execute('SELECT count(DISTINCT country) FROM leads').fetchone()[0],projects=db.execute('SELECT count(*) FROM projects').fetchone()[0],jobs=[dict(r) for r in db.execute('SELECT state,count(*) AS count FROM jobs GROUP BY state')])

    @app.route('/api/projects',methods=['GET','POST'])
    def projects():
        with connect(app.config['DB']) as db:
            if request.method=='POST':
                body=request.get_json(silent=True) or {}
                try:
                    if not isinstance(body,dict) or not isinstance(body.get('cities'),list) or not isinstance(body.get('keywords'),list): raise ValueError()
                    name=str(body['name']).strip();country=str(body['country']).strip();category=str(body['category']).strip()
                    cities=list(dict.fromkeys(x.strip() for x in body['cities'] if isinstance(x,str) and x.strip()))
                    keywords=list(dict.fromkeys(x.strip() for x in body['keywords'] if isinstance(x,str) and x.strip()))
                    depth=int(body.get('depth',5))
                    if not all((name,country,category,cities,keywords)) or not 1<=depth<=10 or len(cities)*len(keywords)>20 or any(len(x)>200 for x in [name,country,category,*cities,*keywords]): raise ValueError()
                    if body.get('consent') is not True: raise ValueError()
                except (KeyError,ValueError,TypeError): abort(400)
                if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','working')").fetchone()[0]+len(cities)*len(keywords)>40:
                    return jsonify(error='صف پر است؛ تا پایان جست‌وجوهای فعلی صبر کنید.'),409
                pid=db.execute('INSERT INTO projects(name,country,category,created) VALUES (?,?,?,?)',(name,country,category,now())).lastrowid
                for city in cities:
                    for kw in keywords:
                        db.execute('INSERT INTO jobs(project_id,city,keyword,depth,email,socials,created,updated) VALUES (?,?,?,?,?,?,?,?)',(pid,city,kw,depth,int(body.get('email') is True),int(body.get('socials') is True),now(),now()))
                return jsonify(id=pid),201
            q='%'+request.args.get('q','')+'%'
            return jsonify([dict(r) for r in db.execute('SELECT p.*,count(j.id) AS total,sum(j.state IN (\'done\',\'empty\',\'failed\')) AS finished,sum(j.count) AS found FROM projects p LEFT JOIN jobs j ON j.project_id=p.id WHERE p.name LIKE ? OR p.country LIKE ? OR p.category LIKE ? GROUP BY p.id ORDER BY p.id DESC',(q,q,q))])

    @app.get('/api/jobs')
    def jobs():
        q='%'+request.args.get('q','')+'%'
        with connect(app.config['DB']) as db:
            return jsonify([dict(r) for r in db.execute('SELECT j.*,p.name AS project_name FROM jobs j JOIN projects p ON p.id=j.project_id WHERE p.name LIKE ? OR j.keyword LIKE ? OR j.city LIKE ? ORDER BY j.id DESC LIMIT 500',(q,q,q))])

    def selected_leads(db):
        q='%'+request.args.get('q','')+'%'; state=request.args.get('status','');project=request.args.get('project','')
        return db.execute('SELECT l.* FROM leads l WHERE (name LIKE ? OR phone LIKE ? OR email LIKE ? OR country LIKE ? OR city LIKE ? OR tags LIKE ? OR notes LIKE ? OR category LIKE ?) AND (?=\'\' OR status=?) AND (?=\'\' OR EXISTS(SELECT 1 FROM project_leads p WHERE p.lead_id=l.id AND p.project_id=?)) ORDER BY l.id DESC',[q]*8+[state,state,project,project]).fetchall()

    @app.get('/api/leads')
    def leads():
        with connect(app.config['DB']) as db: return jsonify([dict(r) for r in selected_leads(db)])

    @app.patch('/api/leads/<int:lead_id>')
    def edit(lead_id):
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or not body or set(body)-set(FIELDS): abort(400)
        if any(not isinstance(v,str) or len(v)>5000 for v in body.values()) or ('status' in body and body['status'] not in STATUSES): abort(400)
        with connect(app.config['DB']) as db:
            if not db.execute('SELECT id FROM leads WHERE id=?',(lead_id,)).fetchone(): abort(404)
            db.execute('UPDATE leads SET '+','.join(k+'=?' for k in body)+' WHERE id=?',[*body.values(),lead_id])
        return jsonify(ok=True)

    @app.get('/api/export/<kind>')
    def export(kind):
        if kind not in ('csv','xlsx'): abort(404)
        with connect(app.config['DB']) as db: rows=selected_leads(db)
        def safe(value):
            value=str(value or '')
            return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value
        if kind=='csv':
            out=io.StringIO();writer=csv.writer(out);writer.writerow(FIELDS)
            writer.writerows([[safe(r[k]) for k in FIELDS] for r in rows])
            data=io.BytesIO(out.getvalue().encode('utf-8-sig'));mime='text/csv; charset=utf-8'
        else:
            wb=Workbook();ws=wb.active;ws.title='سرنخ‌ها';ws.sheet_view.rightToLeft=True;ws.append(FIELDS)
            for row in rows:
                ws.append([str(row[k] or '') for k in FIELDS])
                for cell in ws[ws.max_row]: cell.data_type='s'
            ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
            data=io.BytesIO();wb.save(data);data.seek(0);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return send_file(data,mimetype=mime,as_attachment=True,download_name='azar-dandeh-leads.'+kind)

    return app

def production():
    app=create_app()
    threading.Thread(target=worker,args=(app,),daemon=True).start()
    return app

if __name__=='__main__':
    production().run(host='127.0.0.1',port=8090,debug=False)
