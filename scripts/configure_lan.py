"""Opt-in Let's Encrypt DNS-01 configuration with file-backed credentials."""
import argparse
import ipaddress
import json
from pathlib import Path
import re


def configure(root,hostname,address,image,token_file,*,port=8093):
    root=Path(root).resolve();hostname=hostname.lower();address=str(ipaddress.IPv4Address(address))
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',hostname):raise ValueError('Use a fully qualified hostname')
    if not ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback or ipaddress.ip_address(address).is_unspecified:raise ValueError('Select a private LAN address')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('Pin the Cloudflare-enabled image ID')
    if type(port)is not int or not 1024<=port<=65535:raise ValueError('Use an unprivileged port')
    if not (root/'compose.env').is_file():raise ValueError('Configure the base service first')
    if (root/'lan.env').exists() or (root/'lan').exists():raise FileExistsError('LAN configuration already exists')
    token_file=Path(token_file).resolve();token=token_file.read_text().strip()
    if not re.fullmatch(r'(?:[A-Za-z0-9_-]{35,50}|cf(?:ut|at)_[A-Za-z0-9_-]{32,256})',token):raise ValueError('Invalid Cloudflare credential format')
    target=root/'secrets/cloudflare-api-token'
    if target.exists() and target.resolve()!=token_file:raise FileExistsError('Cloudflare credential already exists')
    lan=root/'lan';lan.mkdir(mode=0o700);(root/'caddy-data').mkdir(mode=0o700,exist_ok=True)
    target.write_text(token);target.chmod(0o600)
    caddy="""{
    admin off
    auto_https disable_redirects
    persist_config off
    default_sni HOSTNAME
    servers {
        protocols h1 h2
    }
    log {
        level ERROR
    }
}
https://HOSTNAME:8443 {
    tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
            disable_http_challenge
            disable_tlsalpn_challenge
            dns cloudflare {file./run/secrets/cloudflare_api_token}
            resolvers 1.1.1.1 1.0.0.1
            propagation_delay 20s
            propagation_timeout -1
        }
    }
    @wrong_host not host HOSTNAME
    respond @wrong_host "Misdirected request" 421
    header {
        -Server
        X-Content-Type-Options nosniff
        Cache-Control no-store
    }
    @handover {
        method GET
        path_regexp handover ^/intake/v1/downloads/[a-f0-9-]+(/files/[a-f0-9-]+)?$
    }
    reverse_proxy @handover ADDRESS:8091
    reverse_proxy api:8090 {
        header_down Location ^http:// https://
    }
}
https://:8443 {
    respond "Misdirected request" 421
}
""".replace('HOSTNAME',hostname).replace('ADDRESS',address)
    (lan/'Caddyfile').write_text(caddy);(lan/'Caddyfile').chmod(0o600)
    report={'origin':'https://'+hostname+':'+str(port),'hostname':hostname,'bind_address':address,'proxy_image':image,
            'issuer':'https://acme-v02.api.letsencrypt.org/directory','challenge':'Cloudflare DNS-01','host_trust_store_changed':False}
    (lan/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    with (root/'lan.env').open('x') as stream:stream.write('SCRIPTALOCAL_LAN_ADDRESS='+address+'\nSCRIPTALOCAL_LAN_PORT='+str(port)+'\nSCRIPTALOCAL_PROXY_IMAGE='+image+'\n')
    (root/'lan.env').chmod(0o600)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('data/service'))
    parser.add_argument('--hostname',required=True);parser.add_argument('--address',required=True);parser.add_argument('--proxy-image',required=True)
    parser.add_argument('--cloudflare-token-file',type=Path,required=True);parser.add_argument('--port',type=int,default=8093)
    args=parser.parse_args();print(json.dumps(configure(args.root,args.hostname,args.address,args.proxy_image,args.cloudflare_token_file,port=args.port),indent=2))


if __name__=='__main__':main()
