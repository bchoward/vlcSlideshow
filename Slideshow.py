#!/opt/local/bin/python2.7


import sys, os, time
import telnetlib, socket
import argparse
import re
import random
import threading, Queue
import subprocess, shlex
sys.path.append("/Users/XXXX/bin") # path to the directory where this script lives
import getch2
import commands

#defaults
vlcPath = "/Applications/VLC.app/Contents/MacOS/VLC" # path to your VLC application's executable
vlcHost = "localhost"
vlcPort = 8080  # port to connect to VLC on
bRandom = True
bRandPos = True
otherVlcOptions = ""
vlcTelnetPrompt = '> '
logpath = "/Users/XXXX/.logfiles/" # where to keep the log of marked files
logprefix = "vlcSlideshow"

playableExts = [".rm", ".avi", ".mpeg", ".mpg", ".divx", ".vob", ".wmv", ".ivx", ".3ivx", ".m4v", ".mkv"]

# create the queues
fq = Queue.Queue()
repoq = Queue.Queue()
cmdq = Queue.Queue(1)


# options processing

parser = argparse.ArgumentParser(description='VLC Slideshow')


parser.add_argument('--interval',  type=int, help='interval in seconds between videos',default=25)
parser.add_argument('--frontOffset',  type=int, help='ignore first part of the movie (percentage, eg 10)',default=30)
parser.add_argument('--backOffset',  type=int, help='ignore last part of the movie (percentage, eg 10)', default=12)
parser.add_argument('--recurse',  action='store_true', default=False, dest='bRecurse', help='Recursively crawl directories')
parser.add_argument('--skip-short',  action='store_true', default=True, dest='bSkipShort', help='Skip playing movies when unable to get length or when movie shorter than interval')
parser.add_argument('--killVLC', action='store_true', default=False, dest='bKillVLC', help='Terminate VLC when quit command received (only terminates VLC if it was launched by this instance)')
parser.add_argument('directory', nargs='+', type=str, help='directories to scan for videos')
args = parser.parse_args()

x = float(args.frontOffset) / float(100)

for r in args.directory:
    repoq.put(r)



def killVLC():
    nullout = open(os.devnull,'w')
    subprocess.call('killall VLC', stdout=nullout, stderr=nullout, shell=True)



def walklevel(some_dir, level=1, walkargs=None):
    some_dir = some_dir.rstrip(os.path.sep)
    assert os.path.isdir(some_dir)
    num_sep = some_dir.count(os.path.sep)
    for root, dirs, files in os.walk(some_dir, **walkargs):
	yield root, dirs, files
	num_sep_this = root.count(os.path.sep)
	if num_sep + level <= num_sep_this:
	    del dirs[:]






class FileScanner(threading.Thread):
    def __init__(self,repoq,fq):
	threading.Thread.__init__(self)
	self.repoq = repoq
	self.fq = fq

    def validExt(self,ext):
	if ext in playableExts:
	    return True
	return False

    def considerFile(self,file,dirpath):
	#print "considering: ", dirpath,file
	fname, ext = os.path.splitext(file)
	if(self.validExt(ext)):
	    self.fq.put(os.path.join(dirpath, file))
	    #print '\tadding', os.path.join(dirpath, file)

	

    def scanError(e):
	raise e

    def run(self):
	while not self.repoq.empty():
	    repo = self.repoq.get()
	    print "adding videos from %s to the playlist" % repo

	    if not os.path.isdir(repo):
		print "ERROR - can't find dir so skipping: %s" % repo
		self.repoq.task_done()
		continue

	    # search and load all files
	    walkargs = { 'followlinks':True, 'onerror':'self.scanError'}
	    if args.bRecurse:
		for dirpath, dirnames, files in os.walk(repo, **walkargs):
		    for f in files:
			self.considerFile(f,dirpath)
	    else:
		for dirpath, dirnames, files in walklevel(repo, walkargs=walkargs, level=1):
		    for f in files:
			self.considerFile(f,dirpath)
	    self.repoq.task_done()
    
	











def loadFiles():

    for i in range(4 if repoq.qsize() > 4 else repoq.qsize()):
	t = FileScanner(repoq,fq)
	#t.setDaemon(True)
	t.start()
	#print "FileScanner launched"

    #wait on the queue until everything has been processed     
    repoq.join()

    #print "videos done loading"
    #print "\tfq contains %d entries" % fq.qsize()







class RepeatTimer(threading.Thread):
    def __init__(self, interval, function, iterations=0, args=[], kwargs={}):
        threading.Thread.__init__(self)
        self.interval = interval
        self.function = function
        self.iterations = iterations
        self.args = args
        self.kwargs = kwargs
        self.ev = threading.Event()
	self.finished = False
 
    def run(self):
        count = 0
        while not self.finished and not self.ev.is_set() and (self.iterations <= 0 or count < self.iterations):
	    while(True):
		ret  = self.ev.wait(self.interval)
		# non-None return if the event was tripped instead of timing out, hence a reset
		# if reset, just sleep longer
		if (not ret) or self.finished:
		    break
		else:
		    self.ev.clear()

            if not self.finished:
                self.function(*self.args, **self.kwargs)
                count += 1
		self.ev.clear()
 
    def cancel(self):
        self.finished = True
	self.ev.set()

    
    def reset(self):
	self.ev.set()





class PlayMonitor(threading.Thread):
    def __init__(self, cmdq, fq):
	threading.Thread.__init__(self)
	self.cmdq = cmdq
	self.fq = cmdq
	self.videos = []
	self.total = 0
	self.current = 0
	self.tn = None
	self.logfile = None
	self.vlcsub = None
	self.connected = False

    def telRead(self):
	while True:
	    try:
		return self.tn.read_until(vlcTelnetPrompt) 
	    except socket.error, e:
		#raise e
		self.connected = False
		if not self.recover():
		    sys.exit()
		continue
	    break
	    

    def recover(self):
	    # restart VLC
	    self.startVLC(bRecover=True)
	    return True
	    
	    

    def launchVLC(self):
	nullin = open(os.devnull)
	nullout = open(os.devnull,'w')
	vargs = "%s --extraintf rc --rc-host %s:%d" % (otherVlcOptions,vlcHost,vlcPort)
	self.vlcsub = subprocess.Popen([vlcPath] + shlex.split(vargs),  close_fds=True, stdin=nullin, stdout=nullout, stderr=nullout)



    def telCmd(self,cmd):
	while True:
	    try:
		self.tn.write("%s\n" % cmd)
		return self.tn.read_until(vlcTelnetPrompt) 
	    except socket.error, e:
		#raise e
		self.connected = False
		if not self.recover():
		    sys.exit()
		continue
	    break


    def startVLC(self, bRecover=False):
	print "connecting to VLC"
	count = 0
	while True:
	    try:
		if count > 3:
		    print "tried three times to connect to VLC, but can't"
		    sys.exit()
		self.tn = telnetlib.Telnet(vlcHost,vlcPort,5)
		self.telRead()
		print "connected."
		if not bRecover:
		    cmdq.put('VLC_connected')
		break
	    except (socket.timeout, socket.error):
		print "error - cannot connect to VLC at %s:%d" % (vlcHost,vlcPort)
		if count == 0:
		    killVLC()
		    time.sleep(1)
		    self.launchVLC()
		print "waiting to try reconnecting..."
		count = count +1
		time.sleep(5)
		continue

	


    def run(self):
	print "monitor running"


	# open log file
	nowstr = str(int(time.time()))
	logfilename = logpath+"/"+logprefix+"-"+nowstr
	self.logfile = open(logfilename, 'w',0)

	# block getting ready messages
	# and hack to start a video while loading...
	connectednow = False
	playlist_ready = False
	while True:
	    m = cmdq.get()
	    if m == 'VLC_connected':
		connectednow = True
		if playlist_ready:
		    break
	    elif m == 'playlist_ready':
		playlist_ready = True
		if connectednow:
		    break
	    else:
		continue

	    # hack to start a file
	    f = fq.get()
	    fq.task_done()
	    fq.put(f)
	    self.telCmd("add %s" % f)
	    print "playing %s" % f
	    if bRandPos:
		self.setRandPos(bSkipShort=False)
	    cmdq.task_done()


	# load the fq into a list
	print "loading playlist"
	random.seed()
	while not fq.empty():
	    self.videos.append(fq.get())
	self.total = len(self.videos)
	if self.total == 0:
	    print "No items in playlist - quitting"
	    sys.exit()
	print "%d videos loaded in playlist" % self.total

	if bRandom:
	    random.shuffle(self.videos)


	while True:
	    c = self.cmdq.get()
	    #print "monitor detects item in cmdq... "

	    if c == 'next':
		self.playNext()
		if bRandPos:
		    self.setRandPos(bSkipShort = args.bSkipShort)
	    elif c == 'prev':
		self.playPrev()
		if bRandPos:
		    self.setRandPos(bSkipShort = args.bSkipShort)
	    elif c.startswith('mark'):
		m = re.match(r"mark(\d)?", c)
		if m.group(1):
		    self.mark(m.group(1))
		else:
		    self.mark()
	    elif c == 'quit':
		self.tn.close()
		if args.bKillVLC:
		    self.vlcsub.kill()
		break
	    else:
		print "command not understood - this isn't supposed to happen"
		print "command = %s" % c
		#sys.exit()
	    self.cmdq.task_done()

    def playNext(self):
	self.telCmd("clear")
	#time.sleep(1)
	f = self.getNext()
	self.telCmd("add %s" % f)
	print "playing %s" % f


    def playPrev(self):
	self.telCmd("clear")
	#time.sleep(1)
	self.current = (self.current-1)%self.total
	f = self.videos[self.current]
	self.telCmd("add %s" % f)
	print "playing %s" % f

    def mark(self,n=0):
	x = self.videos[(self.current - n) % self.total]
	self.logfile.write("%s\n" % x)
	print "marked %s" % x


    def getNext(self):
	self.current = (self.current + 1) % self.total
	return self.videos[self.current]
	

    def getLength(self):
	resp = self.telCmd("get_length")
	l = resp.strip().rstrip(vlcTelnetPrompt).strip()
	if(l.isdigit()):
	    return int(l)
	else:
	    print "response not understood:  resp = #%s# l = #%s#" % (resp, l)
	    return 0



    def setRandPos(self, bSkipShort):
	time.sleep(1)
	count = 0;
	while True:
	    l = self.getLength()
	    count = count + 1
	    # fix constant below to 5000
	    if(count > 20000):
		l = 1
		print "\ttimed out getting length"

	    if l > 0:
		break

	beg = int(float(args.frontOffset)/100*l)
	end = l - int(float(args.backOffset)/100*l) - args.interval
	if end - beg > args.interval:
	    newpos = random.randint(beg,end)
	    #print "\tattempting to seek to random position (%d/%d)" % (newpos,l)
	    self.telCmd("seek %d" % newpos)
	else:
	    print "\tlength too short for seeking."
	    if bSkipShort:
		self.playNext()
	    



def main():
    
    mem = None
	
    def advanceList():
	#print "advancing"
	cmdq.put("next")
	

    def handlekey(ch,mem,timer):
	if mem:
	    if mem == 'b':
		if ch.isdigit():
		    cmdq.put("mark"+ch)
		    print "Marking "+ch+" ago"
	    mem = None

	if ch == 'n':
	    timer.reset()
	    cmdq.put("next")
	elif ch == 'm':
	    cmdq.put("mark")
	    print "Marking current"
	elif ch == 'b':
	    mem = ch
	elif ch == 'p':
	    timer.reset()
	    cmdq.put("prev")

    playmon = PlayMonitor(cmdq,fq)
    playmon.start()
    playmon.startVLC() # tries to connect to VLC, if not, launches it and sleeps, tries to reconnect
    
    loadFiles() # blocks this thread until everything is loaded
    cmdq.put('playlist_ready')
    


    # mnaually start the first movie
    print "starting first video"
    advanceList()


    # set timer
    timer = RepeatTimer(args.interval, advanceList)
    timer.start()
    print "auto-advance set with interval of %d seconds" % args.interval

    # start the ui
    print "UI active"
    quitFlag = False
    while(quitFlag == False):
	ch = getch2.getch() 
	if ch == 'q' or ch == 'z':
	    cmdq.put("quit")
	    timer.cancel()
	    playmon.join()
	    timer.join()
	    if ch == 'z':
		killVLC()
	    return
	    #sys.exit()
	handlekey(ch,mem,timer)
	mem = ch




main()
