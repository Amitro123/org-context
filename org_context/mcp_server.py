"""Experimental bounded local stdio MCP tools, protocol 2025-03-26."""
import argparse
import json
import sys
from . import __version__
from .inventory_context import Context,EVIDENCE_KINDS
from .evidence_pages import READ_TOOL,read_evidence,add_scan_root
from .discovery import DISCOVERY_TOOLS,NAMES as DISCOVERY_NAMES,PAGING,call as discovery_call,snapshot
VERSION='2025-03-26'
MAX_LINE=16384
HARD_LIMIT=1_048_576
PROJECT_SCHEMA={'type':'object','properties':{'project_id':{'type':'string','minLength':1,'maxLength':128}},'required':['project_id'],'additionalProperties':False}
CARD_SCHEMA={'type':'object','properties':{'project_id':{'type':'string','minLength':1,'maxLength':128},'evidence_offset':{'type':'integer','minimum':0,'maximum':100000},'evidence_limit':{'type':'integer','minimum':1,'maximum':20},'evidence_kind':{'type':'string','enum':list(EVIDENCE_KINDS)},'evidence_path':{'type':'string','minLength':1,'maxLength':256}},'required':['project_id'],'additionalProperties':False}
TOOLS=[
 {'name':'project_card','description':'Read scanned project evidence (paged/filterable), dependencies and coverage, or a synthetic reference card.','inputSchema':CARD_SCHEMA},
 {'name':'get_reference_blueprint','description':'Read a synthetic reference; real scanned projects return not_available unless a reference is separately supported.','inputSchema':PROJECT_SCHEMA}]
TOOLS.append({'name':'find_projects_using','description':'Find declared package dependencies in scanned inventory; not proof of runtime use.','inputSchema':{'type':'object','properties':{'technology':{'type':'string','minLength':1,'maxLength':160},'ecosystem':{'type':'string','enum':['python','npm']},**PAGING},'required':['technology'],'additionalProperties':False}})
TOOLS.extend(DISCOVERY_TOOLS)

def error(rid,code,message): return {'jsonrpc':'2.0','id':rid,'error':{'code':code,'message':message}}

class Session:
    def __init__(self,context,evidence_pages=False):
        self.context=context; self.initialized=False; self.ready=False
        self.evidence_pages=evidence_pages
        self.tools=TOOLS+([READ_TOOL] if evidence_pages else [])
    def handle(self,req):
        if isinstance(req,list):
            if not req or len(req)>20 or any(isinstance(x,list) for x in req): return error(None,-32600,'Invalid batch')
            return [r for item in req if (r:=self.handle(item)) is not None] or None
        if not isinstance(req,dict): return error(None,-32600,'Invalid request')
        rid=req.get('id')
        if req.get('jsonrpc')!='2.0' or not isinstance(req.get('method'),str) or ('id' in req and (isinstance(rid,bool) or not isinstance(rid,(int,str)))): return error(None,-32600,'Invalid request')
        method=req['method']; params=req.get('params',{})
        if 'id' not in req:
            if method=='notifications/initialized' and self.initialized: self.ready=True
            return None
        if not isinstance(params,dict): return error(rid,-32602,'Parameters must be an object')
        if method=='initialize':
            if self.initialized: return error(rid,-32600,'Already initialized')
            if not isinstance(params.get('protocolVersion'),str) or not isinstance(params.get('capabilities'),dict) or not isinstance(params.get('clientInfo'),dict): return error(rid,-32602,'Invalid initialization')
            self.initialized=True
            value={'protocolVersion':VERSION,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'org-context','version':__version__}}
        elif method=='ping': value={}
        elif not self.ready: return error(rid,-32002,'Initialize and send notifications/initialized first')
        elif method=='tools/list':
            if params.get('cursor') is not None: return error(rid,-32602,'No cursor supported')
            value={'tools':self.tools}
        elif method=='tools/call':
            name=params.get('name'); arguments=params.get('arguments',{})
            if set(params)-{'name','arguments','_meta'} or not isinstance(name,str) or name not in {t['name'] for t in self.tools}: return error(rid,-32602,'Unknown tool or invalid call')
            if not isinstance(arguments,dict): return error(rid,-32602,'arguments must be an object')
            try:
                if name in DISCOVERY_NAMES: result=discovery_call(self.context,name,arguments)
                elif name=='read_evidence': result=read_evidence(self.context,arguments)
                elif name=='project_card' and getattr(self.context,'db',None) is not None:
                    with snapshot(self.context.db):
                        result=add_scan_root(self.context,self.context.call(name,arguments,include_content=self.evidence_pages))
                else:
                    result=self.context.call(name,arguments,include_content=self.evidence_pages)
                    if name=='project_card': result=add_scan_root(self.context,result)
                text=json.dumps(result,ensure_ascii=True)
                if len(text)>200000: raise ValueError('Result too large; narrow inventory')
                value={'content':[{'type':'text','text':text}],'isError':False}
            except ValueError as exc: value={'content':[{'type':'text','text':str(exc)}],'isError':True}
            except Exception: value={'content':[{'type':'text','text':'Context read failed'}],'isError':True}
        else: return error(rid,-32601,'Method not found')
        return {'jsonrpc':'2.0','id':rid,'result':value}

def serve(context,source,sink,evidence_pages=False):
    session=Session(context,evidence_pages=evidence_pages)
    # Read byte frames before decoding: TextIOWrapper can decode ahead of a newline.
    source=getattr(source,'buffer',source)
    def send(r):
        if r is not None: sink.write(json.dumps(r,ensure_ascii=True)+'\n'); sink.flush()
    while True:
        try:
            line=source.readline(MAX_LINE+1)
            if not line: return
            newline=b'\n' if isinstance(line,bytes) else '\n'
            if len(line)>MAX_LINE:
                total=len(line)
                while not line.endswith(newline) and total<=HARD_LIMIT:
                    line=source.readline(MAX_LINE+1)
                    if not line: break
                    total+=len(line)
                send(error(None,-32600,'Message too large'))
                if total>HARD_LIMIT or not line.endswith(newline): return
                continue
        except UnicodeError:
            # A text-only custom stream cannot promise frame resynchronization.
            send(error(None,-32700,'Parse error')); return
        try:
            if isinstance(line,bytes): line=line.decode('utf-8',errors='strict')
            response=session.handle(json.loads(line))
        except (ValueError,UnicodeError,RecursionError): response=error(None,-32700,'Parse error')
        send(response)

def main():
    for stream in (sys.stdin,sys.stdout):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8',errors='strict')
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',required=True)
    p.add_argument('--evidence-pages',action='store_true',help='Opt in to scanned-evidence excerpts and bounded document paging; may reveal sensitive retained text')
    a=p.parse_args()
    try: context=Context(a.db)
    except Exception: p.exit(2,'Inventory/reference database missing or invalid. Scan or import first.\n')
    try: serve(context,sys.stdin,sys.stdout,evidence_pages=a.evidence_pages)
    finally: context.close()

if __name__=='__main__': main()
