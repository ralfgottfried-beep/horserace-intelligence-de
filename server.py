from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser
import json, re, time, threading
from pathlib import Path
BASE='https://www.deutscher-galopp.de'
CAL=BASE+'/gr/renntage/rennkalender.php'
CACHE={}; LOCK=threading.Lock()
class Parser(HTMLParser):
 def __init__(self): super().__init__(); self.rows=[];self.row=None;self.cell=None;self.link=None;self.links=[]
 def handle_starttag(self,t,a):
  d=dict(a)
  if t=='tr':self.row=[]
  if t in ('td','th') and self.row is not None:self.cell={'text':'','href':''}
  if t=='a':self.link=d.get('href','')
 def handle_data(self,data):
  if self.cell is not None:self.cell['text']+=data
 def handle_endtag(self,t):
  if t in ('td','th') and self.cell is not None:
   self.cell['text']=' '.join(self.cell['text'].split());self.row.append(self.cell);self.cell=None
  if t=='a':
   if self.cell is not None and self.link:self.cell['href']=self.link
   self.link=None
  if t=='tr' and self.row is not None:
   if self.row:self.rows.append(self.row)
   self.row=None

def fetch(url):
 p=urlparse(url)
 if p.scheme!='https' or p.hostname not in ('www.deutscher-galopp.de','deutscher-galopp.de'):raise ValueError('Quelle nicht zugelassen')
 req=Request(url,headers={'User-Agent':'HorseRaceIntelligence/0.3 (private research dashboard)','Accept':'text/html'})
 with urlopen(req,timeout=18) as res:return res.read(1800000).decode('utf-8','replace')
def calendar():
 p=Parser();p.feed(fetch(CAL));out=[]
 for cells in p.rows:
  v=[c['text'] for c in cells]
  if len(v)<9 or not re.match(r'^\d\d\.\d\d\.\d\d$',v[0]):continue
  link=next((c['href'] for c in cells if c['href'] and ('rennen' in c['href'] or 'renn' in c['href'])),None)
  out.append(dict(date=v[0],track=v[1],number=v[2],title=v[3],start=v[4],distance=v[5],category=v[6],prize=v[7],starters=v[8],status=v[9] if len(v)>9 else '',url=urljoin(CAL,link) if link else None))
 return out
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(Path(__file__).parent),**kw)
 def do_GET(self):
  if self.path.startswith('/api/races'):
   try:
    with LOCK:
     if time.time()-CACHE.get('at',0)>300:
      data=calendar();CACHE.update(at=time.time(),data=data)
     else:data=CACHE['data']
    self.send_json({'races':data,'source':CAL,'updated':CACHE['at'],'note':'Öffentliche Rennkalenderdaten; Starterlisten/Jockeys nur auf Quellseite, solange kein verifizierter Detailadapter vorliegt.'})
   except Exception as e:self.send_json({'error':str(e),'races':[]},502)
  else:super().do_GET()
 def send_json(self,obj,status=200):
  b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
if __name__=='__main__':
 print('HorseRace Intelligence DE: http://localhost:8765');ThreadingHTTPServer(('0.0.0.0', int(__import__('os').environ.get('PORT', 10000))),Handler).serve_forever()
