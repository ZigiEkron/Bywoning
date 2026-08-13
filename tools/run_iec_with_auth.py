#!/usr/bin/env python3
import os
import runpy
import sys
import requests

username = os.environ.get('IEC_API_USERNAME', '').strip()
password = os.environ.get('IEC_API_PASSWORD', '')
if not username or not password:
    print('IEC_API_USERNAME / IEC_API_PASSWORD are not configured.', file=sys.stderr)
    sys.exit(3)

OriginalSession = requests.Session
class AuthSession(OriginalSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth = (username, password)

requests.Session = AuthSession
runpy.run_path('tools/iec_byelection_api_extract.py', run_name='__main__')
