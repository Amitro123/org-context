"""Experimental local stdio MCP tools subset, protocol 2025-03-26."""
import argparse
import json
import sys
from .reference import Context

VERSION = "2025-03-26"
MAX_LINE = 16384
SCHEMA = {"type":"object","properties":{"project_id":{"type":"string","pattern":"^[a-z0-9][a-z0-9_-]{0,63}$"}},"required":["project_id"],"additionalProperties":False}
TOOLS = [{"name":name,"description":description,"inputSchema":SCHEMA} for name,description in (
    ("project_card","Read synthetic project metadata, available claims, coverage and unknowns."),
    ("get_reference_blueprint","Read synthetic reference claims and adaptation guidance with source text and hashes. Not live organizational facts."))]

def error(rid,code,message):
    return {"jsonrpc":"2.0","id":rid,"error":{"code":code,"message":message}}

class Session:
    def __init__(self,context):
        self.context=context; self.initialized=False; self.ready=False

    def handle(self,req):
        if isinstance(req,list):
            if not req or len(req)>20:
                return error(None,-32600,"Invalid batch")
            result=[r for item in req if (r:=self.handle(item)) is not None]
            return result or None
        if not isinstance(req,dict):
            return error(None,-32600,"Invalid request")
        rid=req.get("id")
        if req.get("jsonrpc")!="2.0" or not isinstance(req.get("method"),str) or ("id" in req and (isinstance(rid,bool) or not isinstance(rid,(int,str)))):
            return error(None,-32600,"Invalid request")
        method=req["method"]; params=req.get("params",{})
        if "id" not in req:
            if method=="notifications/initialized" and self.initialized:
                self.ready=True
            return None
        if not isinstance(params,dict):
            return error(rid,-32602,"Parameters must be an object")
        if method=="initialize":
            if self.initialized:
                return error(rid,-32600,"Already initialized")
            if not isinstance(params.get("protocolVersion"),str) or not isinstance(params.get("capabilities"),dict) or not isinstance(params.get("clientInfo"),dict):
                return error(rid,-32602,"Invalid initialization")
            self.initialized=True
            value={"protocolVersion":VERSION,"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"org-context-reference","version":"0.1.0"}}
        elif method=="ping":
            value={}
        elif not self.ready:
            return error(rid,-32002,"Initialize and send notifications/initialized first")
        elif method=="tools/list":
            if params.get("cursor") is not None:
                return error(rid,-32602,"No pagination cursor supported")
            value={"tools":TOOLS}
        elif method=="tools/call":
            if set(params)-{"name","arguments","_meta"}:
                return error(rid,-32602,"Unexpected call parameters")
            try:
                result=self.context.call(params.get("name"),params.get("arguments",{}))
                value={"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}],"isError":False}
            except ValueError as exc:
                return error(rid,-32602,str(exc))
            except Exception:
                value={"content":[{"type":"text","text":"Reference read failed"}],"isError":True}
        else:
            return error(rid,-32601,"Method not found")
        return {"jsonrpc":"2.0","id":rid,"result":value}

def serve(context,source,sink):
    session=Session(context)
    while True:
        line=source.readline(MAX_LINE+1)
        if not line:
            return
        if len(line)>MAX_LINE:
            sink.write(json.dumps(error(None,-32600,"Message too large"))+"\n"); sink.flush()
            return
        try:
            req=json.loads(line)
            response=session.handle(req)
        except (ValueError,UnicodeError,RecursionError):
            response=error(None,-32700,"Parse error")
        if response is not None:
            sink.write(json.dumps(response,ensure_ascii=False)+"\n"); sink.flush()

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--db",required=True)
    a=p.parse_args()
    try:
        context=Context(a.db)
    except Exception:
        p.exit(2,"Reference database missing or invalid; import an approved synthetic fixture first.\n")
    try:
        serve(context,sys.stdin,sys.stdout)
    finally:
        context.close()

if __name__ == "__main__":
    main()
