import asyncio
from datetime import datetime, timezone
from glob import glob
import json
from datetime import datetime, timedelta
from os import path

from aiohttp import web, ClientSession
import numpy as np
import cv2

from core import app, active, users, tpl, random_token, Ritual, secrets, struct, assign_twilio_room, room_created, twilio_client, error_handler
from users import connectUserRitual
from widgetry import preload

defaultimg = np.zeros((64,64,3),'uint8')
cv2.circle(defaultimg, (32,32), 24, (0,255,255), thickness=-1)
defaultjpg = bytes(cv2.imencode('.JPG', defaultimg)[1])

try:
    intercom_app_id = secrets['INTERCOM_APP_ID']
except KeyError:
    intercom_app_id = ''

async def homepage(req):
    l = '\n'.join([ '<li><a href="/%s/partake">%s (%s)</a>'%(x,x,active[x].script) for x in active.keys() ])
    s = '\n'.join([ '<option>%s</option>'%(x.replace('examples/','')) for x in glob('examples/*') ])
    html = tpl('html/index.html', list=l, scripts=s)
    return web.Response(text=html, content_type='text/html')

async def favicon(req):
    return web.Response(body=open('favicon.ico','rb').read(), content_type='image/x-icon')

async def ritualPage(req):
    if 'facebookexternalhit' in req.headers.get('User-Agent',''):
        return web.Response(status=403)
    name = req.match_info.get('name','error')
    if name not in active:
        return web.Response(text="Not Found", status=404)
    islead = req.url.path.endswith('/lead')
    if hasattr(active[name],'participants'):
        foundLogin = connectUserRitual(req, active[name], islead)
        if not foundLogin:
            res = web.Response(body=tpl('html/login.html', errorhandler=error_handler), content_type='text/html')
            res.set_cookie('LastRitual', name)
            return res
    clientId = random_token()
    active[name].clients[clientId] = struct(
        chatQueue=asyncio.Queue(),
        lastSeen=datetime.now(),
        isStreamer=('streamer' in req.query),
        room=None,
        name='NotYetNamed',
    )
    if active[name].welcome:
        active[name].clients[clientId].welcomed = False
    if 'fake' in req.query:
        active[name].clients[clientId].lastSeen = datetime(9999,9,9,9,9,9)
        active[name].clients[clientId].welcomed = True
    for datum in active[name].allChats[-50:]:
        active[name].clients[clientId].chatQueue.put_nowait(datum)
    if hasattr(active[name], 'current_video_room'):
        await assign_twilio_room(active[name],clientId)
    else:
        video_room_id = ''
        video_token = ''
    if hasattr(active[name],'participants') or hasattr(active[name], 'current_video_room'):
        for i,task in active[name].reqs.items():
            task.cancel()
    return web.Response(body=tpl('html/client.html',
                                 name=name,
                                 clientId=clientId,
                                 errorhandler=error_handler,
                                 cclass=(
                                     'shrunk'
                                     if hasattr(active[name], 'participants')
                                     or hasattr(active[name], 'current_video_room')
                                     else ''
                                 ),
                                 ratio=str(active[name].ratio),
                                 breserve=active[name].breserve,
                                 islead=str(islead).lower(),
                                 bkgAll=str(active[name].bkgAll).lower(),
                                 rotate=str(active[name].rotate).lower(),
                                 videos=''.join(
                                     f'<video class="hidden" src="{video}" playsinline preload="auto"></video>'
                                     for video in active[name].videos
                                 ),
                                 intercomappid=intercom_app_id,
                        ),
                        content_type='text/html', charset='utf8')


async def welcomed(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    clientId = req.match_info.get('client','')
    if clientId not in active[name].clients:
        return web.Response(status=404)
    active[name].clients[clientId].welcomed = True
    for i,task in active[name].reqs.items():
        task.cancel()
    return web.Response(status=204)


async def nextOrPrevPage(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    isnext = req.url.path.endswith('/nextPage')
    newpage = active[name].page + (isnext and 1 or -1)
    fn = 'examples/%s/%d.svg'%(active[name].script,newpage)
    if not path.exists(fn):
        return web.Response(status=400, text="Attempt to access non-existant page %d"%newpage)
    active[name].page = newpage
    if active[name].state:
        if hasattr(active[name].state,'destroy'):
            active[name].state.destroy()
    elif isnext:
        active[name].rotateSpeakers()
    print("Clearing state")
    active[name].state = None
    for i,task in active[name].reqs.items():
        print("Cancelling %d"%i)
        task.cancel()
    return web.Response(status=204)

async def skipSpeaker(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    active[name].rotateSpeakers()
    for i,task in active[name].reqs.items():
        print("Cancelling %d"%i)
        task.cancel()
    return web.Response(status=204)

async def chatReceive(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    client = active[name].clients.get(req.query.get('clientId'))
    if not client:
        return web.Response(status=401)
    try:
        message = await asyncio.wait_for(client.chatQueue.get(), timeout=25)
    except asyncio.TimeoutError:
        message = None
    return web.Response(text=json.dumps({'message': message}), content_type='application/json')

async def chatSend(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    ritual = active[name]
    form = await req.post()
    text = form.get('text', '')
    if not isinstance(text, str) or text.strip()=='':
        return web.Response(status=400)
    clientId = form.get('clientId')
    if clientId in ritual.clients:
        sender = ritual.clients[clientId].name
    else:
        # I don't *think* this can happen
        sender = 'anon'+clientId
    datum = {'sender': sender, 'text': text}
    for client in ritual.clients.values():
        client.chatQueue.put_nowait(datum)
    ritual.allChats.append(datum)
    return web.Response(status=204)

async def setName(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    ritual = active[name]
    form = await req.post()
    ritual.clients[form['clientId']].name = form['name']
    for i,task in active[name].reqs.items():
        task.cancel()        
    return web.Response(status=204)
    
async def background(req):
    name = req.match_info.get('name')
    sc = active[name].script
    fn = active[name].background
    path = 'examples/%s/%s'%(sc,fn)
    content = open(path,'rb').read()
    return web.Response(body=content, content_type='image/jpeg');   

async def namedimg(req):
    name = req.match_info.get('name')
    fn = req.match_info.get('img')
    sc = active[name].script
    path = 'examples/%s/%s'%(sc,fn)
    content = open(path,'rb').read()
    return web.Response(body=content, content_type='image/jpeg');   

async def mkRitual(req):
    print("mkRitual")
    form = await req.post()
    print(form)
    try:
        name = form['name']
        script = form['script']
    except KeyError:
        return web.Response(text='Bad Form',status=400)
    page = int(form.get('page',1))
    print("good")
    if name in active:
        return web.Response(text='Duplicate',status=400)
    opts = json.loads(open('examples/%s/index.json'%script).read())
    print("very good")
    timestamp = datetime.now(timezone.utc).isoformat()
    active[name] = Ritual(script=script, reqs={}, state=None, page=page, background=opts['background'],
                          bkgAll=opts.get('bkgAll',False), ratio=opts.get('ratio',16/9), welcome=opts.get('welcome'),
                          rotate=opts.get('rotate',True), breserve=opts.get('breserve','233px'), 
                          jpgs=[defaultjpg], jpgrats=[1], clients={}, allChats=[], videos=set())
    for slide_path in glob(f'examples/{script}/*.json'):
        filename = path.basename(slide_path)
        if filename == 'index.json':
            continue
        with open(slide_path) as f:
            slide = json.load(f)
            await preload(active[name],slide)
    if opts['showParticipants'] == 'avatars':
        active[name].participants = []
    elif opts['showParticipants'] == 'video':
        if not twilio_client:
            raise KeyError('participant video requires Twilio secrets')
        active[name].current_video_room = None
        active[name].population_of_current_video_room = 0
        active[name].video_room_lock = asyncio.Lock()
    print("did the thing")
    return web.HTTPFound('/'+name+'/lead')

async def setAvatar(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    ritual = active[name]
    clientId = req.match_info.get('client','')
    if clientId not in ritual.clients:
        return web.Response(status=404)
    client = ritual.clients[clientId]
    print("Set avatar for ritual %s, client %s"%(name,clientId))
    form = await req.post()
    if not hasattr(client,'jpg'):
        for i,task in active[name].reqs.items():
            task.cancel()        
    client.jpg = form['img'].file.read()
    return web.Response(status=204)    

async def getAvatar(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    ritual = active[name]
    clientId = req.match_info.get('client','')
    if clientId not in ritual.clients:
        return web.Response(status=404)
    client = ritual.clients[clientId]
    print("Get avatar for ritual %s, client %s"%(name,clientId))
    if hasattr(client,'jpg'):
        jpg = client.jpg
        ma = 300
    else:
        jpg = open('unknownface.jpg','rb').read();
        ma = 0
    return web.Response(body=jpg, content_type='image/jpg', headers={'Cache-Control': 'max-age=%d'%ma})

async def deleteAvatar(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    ritual = active[name]
    clientId = req.match_info.get('client','')
    if clientId not in ritual.clients:
        return web.Response(status=404)
    client = ritual.clients[clientId]
    print("Delete avatar for ritual %s, client %s"%(name,clientId))
    if hasattr(client,'jpg'):
        del client.jpg
    return web.Response(status=204)  

async def twilioRoomFail(req):
    name = req.match_info.get('name','')
    if name not in active:
        return web.Response(status=404)
    ritual = active[name]
    clientId = req.match_info['client']
    client = ritual.clients[clientId]
    print("client %s(%s) reports fail for room %s"%(clientId,client.name,client.room))
    if not hasattr(client,'twilioCursed'):
        roomId = client.room
        if datetime.now() - room_created[roomId] > timedelta(seconds=10):
            print("  reassigning")
            await assign_twilio_room(ritual, clientId, force_new_room=(roomId==ritual.current_video_room.unique_name))
            for i,task in active[name].reqs.items():
                task.cancel()        
        else:
            print("  declaring cursed")
            client.twilioCursed = True
    else:
        print("  already cursed")
    return web.Response(status=204)

app.router.add_get('/', homepage)
app.router.add_get('/favicon.ico', favicon)
app.router.add_get('/{name}/partake', ritualPage)
app.router.add_get('/{name}/lead', ritualPage)
app.router.add_post('/{name}/nextPage', nextOrPrevPage)
app.router.add_post('/{name}/prevPage', nextOrPrevPage)
app.router.add_post('/{name}/skipSpeaker', skipSpeaker)
app.router.add_get('/{name}/chat/receive', chatReceive)
app.router.add_post('/{name}/chat/send', chatSend)
app.router.add_post('/mkRitual', mkRitual)
app.router.add_get('/{name}/bkg.jpg', background)
app.router.add_get('/{name}/namedimg/{img}', namedimg)
app.router.add_get('/{name}/clientAvatar/{client}', getAvatar)
app.router.add_post('/{name}/clientAvatar/{client}', setAvatar)
app.router.add_delete('/{name}/clientAvatar/{client}', deleteAvatar)
app.router.add_post('/{name}/welcomed/{client}', welcomed)
app.router.add_post('/{name}/setName', setName)
app.router.add_post('/{name}/twilioRoomFail/{client}', twilioRoomFail)
