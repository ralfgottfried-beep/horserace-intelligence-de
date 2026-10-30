from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse, parse_qs
from html.parser import HTMLParser
from pathlib import Path
import json, re, time, threading, os

BASE = 'https://www.deutscher-galopp.de'
CAL = BASE + '/gr/renntage/rennkalender.php'
CACHE = {}
LOCK = threading.Lock()

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables=[]; self.table=None; self.row=None; self.cell=None; self.link=None
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='table':
            if self.table is None:self.table=[]
        if tag=='tr' and self.table is not None:self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell={'text':'','href':'','header':tag=='th'}
        if tag=='a':self.link=attrs.get('href','')
    def handle_data(self, data):
        if self.cell is not None:self.cell['text']+=data
    def handle_endtag(self,tag):
        if tag=='a':
            if self.cell is not None and self.link:self.cell['href']=self.link
            self.link=None
        if tag in ('td','th') and self.cell is not None:
            self.cell['text']=' '.join(self.cell['text'].split());self.row.append(self.cell);self.cell=None
        if tag=='tr' and self.row is not None:
            if self.row:self.table.append(self.row)
            self.row=None
        if tag=='table' and self.table is not None:
            if self.table:self.tables.append(self.table)
            self.table=None

def fetch(url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in ('www.deutscher-galopp.de','deutscher-galopp.de'):
        raise ValueError('Quelle nicht zugelassen')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; HorseRaceIntelligence/0.4; research)','Accept':'text/html'})
    with urlopen(req,timeout=18) as res:
        final=urlparse(res.geturl())
        if final.scheme!='https' or final.hostname not in ('www.deutscher-galopp.de','deutscher-galopp.de'):
            raise ValueError('Weiterleitung auf nicht zugelassene Quelle')
        return res.read(1800000).decode('utf-8','replace')

def calendar():
    p=TableParser();p.feed(fetch(CAL));out=[]
    for table in p.tables:
        for cells in table:
            v=[c['text'] for c in cells]
            if len(v)<9 or not re.match(r'^\d\d\.\d\d\.\d\d$',v[0]):continue
            link=next((c['href'] for c in cells if c['href'] and 'renn' in c['href'].lower()),None)
            out.append(dict(date=v[0],track=v[1],number=v[2],title=v[3],start=v[4],distance=v[5],category=v[6],prize=v[7],starters=v[8],status=v[9] if len(v)>9 else '',url=urljoin(CAL,link) if link else None))
    return out

# Strict header mapping: unknown layouts are not guessed.
FIELDS={
 'horse':['pferd','pferdename','horse'],
 'jockey':['jockey','reiter','reiterin'],
 'trainer':['trainer','trainerin'],
 'number':['nr.','nr','startnr.','startnummer'],
 'weight':['gewicht','gew.'],
 'box':['box','startbox'],
 'odds':['quote','siegquote','sieg-quote','siegwette']
}
def norm(s):return re.sub(r'\s+',' ',s.lower().strip().replace(':',''))
def starter_details(url):
    p=TableParser();p.feed(fetch(url));candidates=[]
    for table in p.tables:
        for i,row in enumerate(table[:5]):
            labels=[norm(c['text']) for c in row]
            mapping={}
            for key,aliases in FIELDS.items():
                for j,label in enumerate(labels):
                    if label in aliases:
                        mapping[key]=j;break
            if 'horse' not in mapping or 'jockey' not in mapping:continue
            entries=[]
            for cells in table[i+1:]:
                vals=[c['text'] for c in cells]
                if max(mapping.values())>=len(vals):continue
                item={key:vals[j] for key,j in mapping.items()}
                if not item['horse'] or not item['jockey']:continue
                entries.append(item)
            if entries:candidates.append(entries)
    starters=max(candidates,key=len) if candidates else []
    return {'starters':starters,'source':url,'updated':time.time(),'verified':bool(starters),
            'note':'Starterliste anhand eindeutiger Tabellenüberschriften erkannt.' if starters else 'Auf dieser Seite keine eindeutig zuordenbare Pferd/Jockey-Tabelle gefunden. Offizielle Details bitte direkt öffnen.',
            'odds_note':'Quoten sind Momentaufnahmen und können sich ändern; fehlende Quoten werden nicht geschätzt.'}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kw):super().__init__(*args,directory=str(Path(__file__).parent),**kw)
    def do_GET(self):
        route=urlparse(self.path)
        if route.path=='/api/races':
            try:
                with LOCK:
                    if time.time()-CACHE.get('at',0)>300:
                        data=calendar();CACHE.update(at=time.time(),data=data)
                    else:data=CACHE['data']
                return self.send_json({'races':data,'source':CAL,'updated':CACHE['at']})
            except Exception as e:return self.send_json({'error':str(e),'races':[]},502)
        if route.path=='/api/details':
            try:
                target=parse_qs(route.query).get('url',[''])[0]
                if not target:return self.send_json({'error':'Rennlink fehlt'},400)
                # Only fetch race URLs obtained from the calendar, not arbitrary external URLs.
                with LOCK:
                    known={r['url'] for r in CACHE.get('data',[]) if r.get('url')}
                if target not in known:return self.send_json({'error':'Rennlink nicht im aktuellen Kalender. Bitte Renntermine aktualisieren.'},400)
                with LOCK:
                    cached=CACHE.get('detail:'+target)
                    if cached and time.time()-cached['updated']<180:return self.send_json(cached)
                result=starter_details(target)
                with LOCK:CACHE['detail:'+target]=result
                return self.send_json(result)
            except Exception as e:return self.send_json({'error':str(e),'starters':[]},502)
        return super().do_GET()
    def send_json(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)

if __name__=='__main__':
    port=int(os.environ.get('PORT','8765'))
    print(f'HorseRace Intelligence DE v0.4: 0.0.0.0:{port}',flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
