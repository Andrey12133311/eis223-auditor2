"""Read-only EIS SOAP connectivity/auth probe. Never logs tokens or response bodies."""
import datetime
import os
import threading
import uuid

def probe():
    token=os.getenv("EIS_TOKEN","").strip()
    if not token:
        print("V292 EIS_SOAP_NO_TOKEN",flush=True)
        return
    import httpx
    endpoint=os.getenv("EIS_SOAP_ENDPOINT","https://int.zakupki.gov.ru/eis-integration/services/getDocsIP")
    if endpoint != "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP":
        print("V292 EIS_SOAP_INVALID_ENDPOINT",flush=True)
        return
    body=('<?xml version="1.0" encoding="utf-8"?>'
          '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
          'xmlns:ws="http://zakupki.gov.ru/fz44/get-docs-ip/ws">'
          '<soapenv:Header/>'
          '<soapenv:Body><ws:getDocsByReestrNumberRequest>'
          '<index><id>'+str(uuid.uuid4())+'</id><createDateTime>'+
          datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")+
          '</createDateTime><mode>PROD</mode></index>'
          '<selectionParams><subsystemType>RI223</subsystemType>'
          '<reestrNumber>32616440427</reestrNumber></selectionParams>'
          '</ws:getDocsByReestrNumberRequest></soapenv:Body></soapenv:Envelope>')
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0,connect=10.0),follow_redirects=False,trust_env=False) as client:
            r=client.post(endpoint,content=body.encode(),headers={
                "Content-Type":"text/xml; charset=utf-8",
                "SOAPAction":'"http://zakupki.gov.ru/fz44/queue/ws/get-docs-ip"',
                "individualPerson_token":token,
            })
        import xml.etree.ElementTree as ET
        try:
            root=ET.fromstring(r.content[:1000000])
            tags=[node.tag.split("}")[-1].lower() for node in root.iter()]
            fault="fault" in tags
            archive=any("archive" in t or "url" in t or "file" in t for t in tags)
            print("V292 EIS_SOAP_PROBE",r.status_code,"xml",True,"fault",fault,"archive_fields",archive,flush=True)
        except ET.ParseError:
            print("V292 EIS_SOAP_PROBE",r.status_code,"xml",False,flush=True)
    except httpx.ConnectTimeout:
        print("V292 EIS_SOAP_TCP_CONNECT_TIMEOUT current-domain",flush=True)
    except httpx.ReadTimeout:
        print("V292 EIS_SOAP_HTTP_READ_TIMEOUT current-domain",flush=True)
    except httpx.TimeoutException:
        print("V292 EIS_SOAP_TIMEOUT current-domain",flush=True)
    except httpx.HTTPError as e:
        print("V292 EIS_SOAP_NETWORK_ERROR",type(e).__name__,flush=True)

def start():
    threading.Thread(target=probe,name="eis-soap-probe",daemon=True).start()
