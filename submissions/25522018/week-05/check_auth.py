"""Exercise four graded authorization failures; records real HTTP/tool responses."""
import os, pathlib
from host import admin,request,META,MCP,BASE

def main():
    s={'id':'auth-1','budget':50,'reserve':70}
    a=admin('open',{'condition':'server_inject','scenario':s})
    b=admin('open',{'condition':'server_inject','scenario':s})
    nid=a['negotiation_id']; other=b['negotiation_id']
    params={'_meta':META}
    status,headers,result=request(BASE+'/mcp',
        {'jsonrpc':'2.0','id':1,'method':'tools/list','params':params},
        {'Mcp-Method':'tools/list'})
    lines=['1. Missing bearer token: HTTP '+str(status)+'; WWW-Authenticate: '+str(headers.get('WWW-Authenticate'))]
    buyer=MCP(a['tokens']['buyer']); seller=MCP(a['tokens']['seller'])
    j=buyer.call('get_negotiation',{'negotiation_id':other})
    lines.append('2. Other negotiation_id: isError='+str(j['isError'])+'; '+j['content'][0]['text'])
    j=seller.call('propose',{'negotiation_id':nid,'price':70})
    lines.append('3. Seller move out of turn: isError='+str(j['isError'])+'; '+j['content'][0]['text'])
    j=buyer.call('propose',{'negotiation_id':nid,'price':80})
    lines.append('4. Buyer price above token limit in server condition: isError='+str(j['isError'])+'; '+j['content'][0]['text'])
    (pathlib.Path(__file__).parent/'auth_checks.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__=='__main__':main()
