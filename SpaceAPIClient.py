import json
import requests
from requests.auth import HTTPDigestAuth

BIGREDINPUT = 4 #CUSTOM VALUE FOR FARSET LABS
DOORINPUT = 3 #CUSTOM VALUE FOR FARSET LABS

class AuthenticationException(Exception):
    pass

class APIException(Exception):
    pass

def strbool(string):
    return bool(string == "True")

class client():
    def __init__(self,authpair=None,base=None):
        if authpair is not None:
            self.authpair=tuple(authpair)
        else:
            self.authpair=None
        if base is None:
            base = BASE_URL
        self.base = base

    def get(self, path, **params):
        path = "%s%s"%(self.base,path)
        return requests.get( path, params = params)

    def authGet(self, path, **params):
        path = "%s%s"%(self.base,path)
        if self.authpair is not None:
            return requests.get(path,auth=HTTPDigestAuth(*self.authpair))
        else:
            raise AuthenticationException

    def authPut(self, path, **params):
        path = "%s%s"%(self.base,path)
        if self.authpair is not None:
            return requests.put(path,auth=HTTPDigestAuth(*self.authpair), params=params)
        else:
            raise AuthenticationException

    def buttonDownState(self):
        r=self.get('/board',type='digital',channel=BIGREDINPUT)
        r=r.json
        try:
            if 'value' in r.keys():
                value = strbool(r['value'])
            else:
                value = None
                raise ValueError("Button State Cannot Be Determined!%s"%r)
        except AttributeError as E:
            raise ValueError("Button State Cannot Be Determined!%s:%s"%(r,E))
        return value

    def doorClosedState(self):
        r=self.get('/door')
        r=r.json
        try:
            if 'value' in r.keys():
                value = strbool(r['value'])
            else:
                value = None
                raise ValueError("Door State Cannot Be Determined!%s"%r)
        except AttributeError as E:
            raise ValueError("Door State Cannot Be Determined!%s:%s"%(r,E))
        return value

    def openDoor(self):
        r=self.authGet('/door/open')
        return r.json

    def spaceState(self):
        r=self.get('/space')
        return r.json

    def set_open_state(self,open_state):
        if isinstance(open_state,bool):
            open_state = str(open_state)
        r=self.authPut('/space',state="%s"%open_state)
        new_state = str(r.json['value'])
        if new_state == open_state:
            return r
        else:
            raise APIException("Returned Value does not match PUT value:%s,%s"%(new_state,open_state))


