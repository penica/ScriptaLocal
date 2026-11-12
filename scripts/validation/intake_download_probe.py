"""Read-only HTTP verification; credential remains in its private ticket file."""
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler,Request,build_opener

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def main():
    ticket=json.loads(Path(sys.argv[1]).read_text())
    opener=build_opener(NoRedirect())
    def get(url,token=None):
        headers={'Authorization':'Bearer '+token} if token else {}
        with opener.open(Request(url,headers=headers),timeout=10) as response:
            return response.read(),response.headers
    url=ticket['manifest_url'];token=ticket['download_token']
    raw,_=get(url,token);manifest=json.loads(raw)
    assert token not in raw.decode() and manifest['files']==ticket['files']
    files=[]
    for item in manifest['files']:
        path=url+'/files/'+item['file_id']
        content,headers=get(path,token)
        assert len(content)==item['bytes'] and hashlib.sha256(content).hexdigest()==item['sha256']
        assert headers['X-Content-SHA256']==item['sha256'] and headers['Cache-Control']=='no-store'
        try:get(path)
        except HTTPError as exc:assert exc.code==404
        else:raise AssertionError('Unauthenticated read permitted')
        files.append({key:item[key] for key in ['filename','bytes','sha256']})
    print(json.dumps({'base_url':url.split('/intake/')[0],'manifest':'passed','snapshot_checksums':'passed',
                      'unauthorized_file_read':'rejected','files':files},indent=2))

if __name__=='__main__':main()
