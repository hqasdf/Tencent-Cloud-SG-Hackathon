"""Repeatable local-only gateway smoke; build frontend first. No live AI."""
import os, ssl, socket, json, secrets, base64, subprocess, threading, time, ipaddress
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
root=Path(__file__).resolve().parents[2]; scratch=root/'backend/.pytest-scratch/security-audit/gateway';scratch.mkdir(parents=True,exist_ok=True)
import argparse
parser=argparse.ArgumentParser(description='Local synthetic HTTPS gateway regression; no browser or AI calls.')
parser.add_argument('--caddy', type=Path, required=True)
caddy=parser.parse_args().caddy.resolve()
assert caddy.is_file(), 'Verified local Caddy binary unavailable'
seen=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        seen.append({'method':'GET','authorization_forwarded':bool(self.headers.get('Authorization'))})
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"synthetic":true}')
    def do_POST(self):
        seen.append({'method':'POST','authorization_forwarded':bool(self.headers.get('Authorization'))})
        self.rfile.read(int(self.headers.get('Content-Length','0')));self.send_response(200);self.end_headers();
        try: self.wfile.write(b'{"synthetic":true}')
        except (ConnectionError,OSError): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler); threading.Thread(target=server.serve_forever,daemon=True).start()
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'127.0.0.1')])
now=datetime.now(timezone.utc)
cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256()))
certpath=scratch/'synthetic-cert.pem';keypath=scratch/'synthetic-key.pem'
certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM));keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
password=secrets.token_urlsafe(24)
hashed=subprocess.check_output([str(caddy),'hash-password','--plaintext',password],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),text=True).strip()
config=(root/'deploy/Caddyfile').read_text().replace('admin off','admin off\n    persist_config off\n    auto_https disable_redirects\n    storage file_system {\n        root "' + str(scratch/'storage').replace('\\','/') + '"\n    }')
config=config.replace('https://{$DOMAIN} {', f'https://{{$DOMAIN}} {{\n    bind 127.0.0.1\n    tls "{certpath.as_posix()}" "{keypath.as_posix()}"')
config=config.replace('api:8000',f'127.0.0.1:{server.server_port}').replace('root * /srv',f'root * "{(root / "frontend/dist").as_posix()}"')
configpath=scratch/'Caddyfile';configpath.write_text(config)
env=dict(os.environ,DOMAIN=f'127.0.0.1:{port}',DEMO_USER='synthetic-viewer',DEMO_PASSWORD_HASH=hashed)
validation=subprocess.run([str(caddy),'validate','--config',str(configpath),'--adapter','caddyfile'],env=env,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
assert validation.returncode==0,'Gateway config validation failed'
log=(scratch/'runtime.log').open('wb')
proc=subprocess.Popen([str(caddy),'run','--config',str(configpath),'--adapter','caddyfile'],env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
context=ssl.create_default_context(cafile=str(certpath)); base=f'https://127.0.0.1:{port}';authorization='Basic '+base64.b64encode(('synthetic-viewer:'+password).encode()).decode()
def request(path,*,auth=False,origin=None,body=None,method=None):
    headers={'Authorization':authorization} if auth else {}
    if origin:headers['Origin']=origin
    if body is not None:headers['Content-Type']='application/json'
    try:
        response=urlopen(Request(base+path,headers=headers,data=body,method=method),context=context,timeout=3)
    except HTTPError as e:response=e
    with response:return response.status,dict(response.headers),response.read()
try:
    for _ in range(100):
        try:request('/');break
        except (OSError,ssl.SSLError):time.sleep(.05)
    assert request('/')[0]==401
    assert request('/api/health')[0]==401
    assert request('/api/cases/DISP-005/resolution/run',method='POST')[0]==401
    status,headers,body=request('/',auth=True);assert status==200 and b'<!doctype html>' in body
    assert 'Content-Security-Policy' in headers and headers.get('Cache-Control')=='no-store'
    assert request('/api/health',auth=True)[0]==200
    count=len(seen);assert request('/api/cases/DISP-005/resolution/run',auth=True,origin='https://untrusted.invalid',body=b'{}')[0]==403;assert len(seen)==count
    assert request('/api/cases/DISP-005/resolution/run',auth=True,origin=base,body=b'{}')[0]==200
    assert not any(item['authorization_forwarded'] for item in seen)
    assert request('/api/test',auth=True,body=b'x'*33000)[0]==413
    assert request('/backend/intake.db',auth=True)[2].startswith(b'<!doctype html>')
    result={'gateway_config_valid':True,'unauthorized_ui_api_paid_action':401,'cross_origin_write':403,'same_origin_write':200,'oversized_body':413,'authorization_stripped':True,'security_headers':True,'private_database_not_served':True,'tls':'synthetic locally trusted test certificate only','provider_calls':0}
    (scratch/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:
    proc.terminate();proc.wait(timeout=10);server.shutdown();server.server_close();log.close()
