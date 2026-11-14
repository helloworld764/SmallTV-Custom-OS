"""Read-only software device probe. No board, live credentials or UI dependencies."""
import argparse,getpass,json,ssl,time,urllib.error,urllib.parse,urllib.request,warnings
from uuid import UUID
from http.client import HTTPException

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class Probe:
    def __init__(self,url,device_id,key,ca_file=None,timeout=5):
        parsed=urllib.parse.urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Expected server origin')
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','::1')):
            raise ValueError('HTTPS required outside loopback')
        if not parsed.hostname or any(ch.isspace() for ch in url) or '\\' in url:raise ValueError('Invalid origin')
        self.origin=url.rstrip('/');self.device_id=str(UUID(device_id));self.key=key;self.timeout=timeout;self.cursor=0
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))
        self.state='UNKNOWN';self.snapshot=None;self.seen=False

    def read(self,suffix,limit=16384):
        request=urllib.request.Request(f'{self.origin}/api/v1/devices/{self.device_id}/display{suffix}',headers={'Authorization':'Bearer '+self.key})
        with self.opener.open(request,timeout=self.timeout) as response:
            if response.status!=200:raise ValueError('Unexpected status')
            raw=response.read(limit+1)
        if len(raw)>limit:raise ValueError('Response budget exceeded')
        return json.loads(raw)

    def poll(self):
        try:
            snapshot=self.read('');page=self.read('/events?after='+str(self.cursor))
            if (not isinstance(snapshot,dict) or not isinstance(page,dict)
                or type(snapshot.get('schema_version')) is not int or snapshot['schema_version']!=1
                or snapshot.get('device_id')!=self.device_id
                or snapshot.get('screen') not in ('clock','weather','codex','tamagotchi','markets','monitor')
                or type(page.get('schema_version')) is not int or page['schema_version']!=1
                or page.get('device_id')!=self.device_id):
                raise ValueError('Unsupported protocol')
            cursor=page['next_cursor']
            if type(cursor) is not int or not 0<=cursor<=9007199254740991:raise ValueError('Invalid cursor')
            # Server restore may reset sequence; trust its reset contract instead of replaying old events.
            self.cursor=cursor;self.snapshot=snapshot;self.state='ONLINE';self.seen=True
        except urllib.error.HTTPError as error:
            if error.code in (401,403):self.snapshot=None;self.cursor=0
            error.close();self.state='OFFLINE' if self.seen else 'UNKNOWN'
        except (OSError,ValueError,KeyError,TypeError,RecursionError,HTTPException,urllib.error.URLError):
            self.state='OFFLINE' if self.seen else 'UNKNOWN'
        return {'status':self.state,'screen':self.snapshot['screen'] if self.snapshot else None,'cursor':self.cursor}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8001');parser.add_argument('--device-id',required=True)
    parser.add_argument('--ca-file');parser.add_argument('--cycles',type=int,default=1);parser.add_argument('--interval',type=float,default=5);args=parser.parse_args()
    if not 1<=args.cycles<=100000 or not 0.1<=args.interval<=3600:parser.error('Invalid bounded run')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            key=getpass.getpass('Device key (hidden): ')
        probe=Probe(args.url,args.device_id,key,args.ca_file)
        good=True
        for n in range(args.cycles):
            result=probe.poll();print(json.dumps(result));good=good and result['status']=='ONLINE'
            if n+1<args.cycles:time.sleep(args.interval)
        return 0 if good else 1
    except (OSError,ValueError,EOFError,getpass.GetPassWarning):print('{"status":"probe_failed"}');return 1
if __name__=='__main__':raise SystemExit(main())
