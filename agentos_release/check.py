from pathlib import Path
from html.parser import HTMLParser
base=Path(__file__).parent/'claude_clone/static'
class P(HTMLParser):
 def __init__(self): super().__init__();self.ids=set();self.dupes=[];self.refs=[]
 def handle_starttag(self,t,attrs):
  attrs=dict(attrs)
  if attrs.get('id'):
   if attrs['id'] in self.ids:self.dupes.append(attrs['id'])
   self.ids.add(attrs['id'])
  if t in ('script','link'):
   u=attrs.get('src') or attrs.get('href')
   if u and u.startswith('/static/'):self.refs.append(u)
p=P();p.feed((base/'index.html').read_text())
assert not p.dupes,p.dupes
for ref in p.refs: assert (base/ref.removeprefix('/static/')).exists(),ref
for id in ['agentInspector','aiTitle','aiSubtitle','aiBody','aiClose','fileview','ctxFill','compactBtn']:
 assert id in p.ids,id
print('HTML check: OK, local resources:',p.refs)
