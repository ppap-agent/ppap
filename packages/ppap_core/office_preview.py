"""Read-only Docs API config and a narrowly scoped, ten-minute file capability."""
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

OFFICE_TYPES = {**dict.fromkeys(('doc', 'docx', 'odt', 'rtf'), 'word'),
                **dict.fromkeys(('xls', 'xlsx', 'xlsm', 'ods', 'csv'), 'cell'),
                **dict.fromkeys(('ppt', 'pptx', 'odp'), 'slide')}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def _sign(payload: dict, secret: str) -> str:
    header = _b64(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    message = header + '.' + body
    return message + '.' + _b64(hmac.digest(secret.encode(), message.encode(), 'sha256'))


def read_ticket(token: str, secret: str) -> dict:
    try:
        if len(secret) < 32 or len(token) > 4096:
            raise ValueError()
        header, body, signature = token.split('.')
        expected = _b64(hmac.digest(secret.encode(), (header+'.'+body).encode(), 'sha256'))
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if payload.get('purpose') != 'ppap-office-file' or not time.time() < payload['exp'] <= time.time()+601:
            raise ValueError()
        if not all(isinstance(payload.get(key), str) and payload[key] for key in ('file_id','version_id','tenant_id')):
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        raise ValueError('invalid_preview_ticket') from exc


def preview_config(settings, version) -> dict:
    extension = Path(version.original_filename).suffix.lower().lstrip('.')
    if extension not in OFFICE_TYPES:
        return {'available': False, 'notice': '此格式使用原件或文字预览。'}
    if not settings.onlyoffice_url or not settings.onlyoffice_api_url or len(settings.onlyoffice_secret) < 32:
        return {'available': False, 'notice': '未配置 Office 排版预览，当前显示文字摘录。'}
    for url in (settings.onlyoffice_url, settings.onlyoffice_api_url):
        parts = urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('invalid_onlyoffice_url')
    expires = int(time.time()) + 600
    ticket = _sign({'purpose':'ppap-office-file', 'exp':expires, 'file_id':version.file_id,
                    'version_id':version.id, 'tenant_id':version.tenant_id}, settings.onlyoffice_secret)
    download = settings.onlyoffice_api_url.rstrip('/') + '/office/content?' + urlencode({'ticket':ticket})
    key = hashlib.sha256((settings.public_api_url+'|'+version.tenant_id+'|'+version.id).encode()).hexdigest()
    config = {'documentType':OFFICE_TYPES[extension], 'width':'100%', 'height':'100%',
              'document':{'key':key, 'title':version.original_filename[:128], 'fileType':extension, 'url':download,
                          'permissions':{'edit':False, 'review':False, 'comment':False, 'fillForms':False, 'download':False, 'print':False}},
              'editorConfig':{'mode':'view', 'lang':'zh', 'customization':{'compactHeader':True}}, 'exp':expires}
    config['token'] = _sign(config, settings.onlyoffice_secret)
    return {'available':True, 'script_url':settings.onlyoffice_url.rstrip('/')+'/web-apps/apps/api/documents/api.js', 'config':config}
