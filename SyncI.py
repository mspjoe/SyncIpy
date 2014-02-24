#!/usr/bin/python
import sqlite3, glob, json, sys, inspect, subprocess, hashlib, time, os, signal

from SI_flickr import SI_flickr
from datetime import datetime,timedelta


#GLOBALS
cfgdir = "/etc/SyncIpy/"
dbdir = "."
verbose = 2

cur_pub = None

if len(sys.argv) == 2:
	cfgdir = sys.argv[0]
elif len(sys.argv) > 2:
	print "Too many arguments provided.  A single path where config files can be found is the only argument"

pubs = []

halt = 0


#OPEN SQL
conn = sqlite3.connect('SyncIpy.db')
c = conn.cursor()
c2 = conn.cursor()

#TABLE GENERATION AND UPDATE FUNCTIONS
def gen_tables():

	pubs[:] = []
	c.execute('CREATE TABLE IF NOT EXISTS PHOTOS ( ' +
			  '"ID" INTEGER PRIMARY KEY AUTOINCREMENT,' +
			  '"PK" TEXT,'  +
		      '"PATH" TEXT,'   +
		      '"FILE" TEXT,'   +
			  '"STATUS" TEXT,' +
			  '"MDTTM" TEXT,' +
		      '"EXIF" TEXT)')
	c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id_photo on PHOTOS (PK ASC)')
	
	c.execute('CREATE TABLE IF NOT EXISTS PUBLISHERS ( ' +
			   '"ID" INTEGER PRIMARY KEY AUTOINCREMENT,' +
			   '"FILE" TEXT,'  +
			   '"TYPE" TEXT,'  +
		       '"ENABLED" TEXT,'   +
			   '"CONFIG" TEXT)')
	
	c.execute('CREATE UNIQUE INDEX IF NOT EXISTS "id_pubs" on PUBLISHERS ("FILE" ASC)')

	for config_f in glob.glob(cfgdir+"*.pub"):
#		print config_f
		try:
			j = json.load(open(config_f))
		except Exception, e: 
			print type(e)
			print '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)

		
		c.execute('INSERT OR IGNORE INTO PUBLISHERS (FILE) VALUES(?)',[config_f])
		c.execute('UPDATE PUBLISHERS SET TYPE=?, ENABLED=?, CONFIG=? WHERE FILE=?',[j['TYPE'],j['ENABLED'],json.dumps(j),config_f])
		if j['ENABLED'] == True:# and c.execute('SELECT COUNT(*) FROM :	  
			tID = c.execute('SELECT ID FROM PUBLISHERS WHERE FILE=?',[config_f]).fetchone()[0]
#			if c.execute('SELECT COUNT(*) FROM sqlite_master where type="table" and name="PB'+str(tID) +'"').fetchone()[0]:
			pubs.append(tID)
			
	conn.commit()



#DIR SCANNING FUNCTIONS
def read_exifjson():
	j = []
	pub_dir = {}
	modified_images = set()
	missing_images = set()
	sizes = {}

	c.execute('SELECT PATH,FILE,MDTTM FROM PHOTOS')
	for photo in c:
		sizes[os.path.join(photo[0],photo[1])]=photo[2]
		missing_images.add(os.path.join(photo[0],photo[1]))

	c.execute('SELECT ID,CONFIG FROM PUBLISHERS WHERE ENABLED=1')
	for pub in c:
		PBT = "PB" + str(pub[0])
		cfg = json.loads(pub[1])
		dir = cfg['PATH']
		pub_dir[PBT]=dir
		ext = tuple(cfg['EXT'].split("|"))
		
		utcnowdttm = int((datetime.utcnow()-datetime(1970, 1, 1)).total_seconds()-30)
		
		for root, dirs, files in os.walk(dir):
			files = [f for f in files if not f[0] == '.']
			dirs[:] = [d for d in dirs if not d[0] == '.']
			files = [ f for f in files if f.endswith(ext) ]	 
			for file in files:
				pj = os.path.join(root,file)
				mdttm = int(os.path.getmtime(pj))
			
				if pj in missing_images:
					missing_images.remove(pj)
				if (pj not in sizes or sizes[pj] != mdttm) and mdttm < utcnowdttm:
					modified_images.add(pj)

	for i in missing_images:
		(missing_path, missing_file) = os.path.split(i)
		c.execute('UPDATE PHOTOS SET STATUS="RM" WHERE FILE=? AND PATH=?',[missing_file,missing_path])
					
	if len(modified_images) > 0:
		cmd = ['exiftool','-fast','-j']
		cmd.extend(modified_images)
		out = subprocess.check_output(cmd)
		j = json.loads(out)

	for i in j:
		sts='OK'
		id=""
		pj = os.path.join(i["Directory"],i["FileName"])
		dt=int(os.path.getmtime(pj))
		
		c.execute('UPDATE OR IGNORE PHOTOS SET STATUS="RM" WHERE PATH=? AND FILE=?',[i["Directory"],i["FileName"]])
		
		if 'Error' in i:
			print i["FileName"] + ":" + "exiftool reported error" + i['Error']
			prehash = 'ERROR:'+pj
			sts='ER'
		elif 'OriginalDocumentID' in i:
			try:
				prehash=i["OriginalDocumentID"]+":"+pj
				
			except Exception, e: 
				print i["FileName"]
				print '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)
		else:
			print i["FileName"] + ":" + "no OriginalDocumentID tag present.  Using NONE" 
			prehash = 'NONE:'+pj
		id = str(hashlib.sha256(prehash).hexdigest()) 
		c.execute('REPLACE INTO PHOTOS (PK, PATH, FILE, STATUS, MDTTM, EXIF) VALUES (?,?,?,?,?,?)',[id,i['Directory'],i['FileName'],sts,dt,json.dumps(i)])
#		print '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe()))
		if sts == 'OK':
			for pub in pubs:
				PBT = "PB"+str(pub)
				if c.execute('SELECT COUNT(*) FROM sqlite_master where type="table" and name="'+ PBT +'"').fetchone()[0] and pj.startswith(pub_dir[PBT]):
					c.execute('UPDATE OR IGNORE '+PBT+' SET STATUS="XO" WHERE PK=?',[id])
					c.execute('INSERT OR IGNORE INTO '+PBT+' (PK,SK,STATUS) VALUES (?,?,?)',[id,"TEMPSK_"+id,"NW"])

	conn.commit()
	return

def run_pubs():
	global cur_pub
	if verbose > 4: 	print c.execute('SELECT COUNT(PK),STATUS from PHOTOS GROUP BY STATUS').fetchall()
	for pub in pubs:
		if halt == 1: return 
		PBT= "PB"+str(pub)
		p = (c.execute('SELECT CONFIG FROM PUBLISHERS WHERE ID=?',[pub]).fetchall()[0])
		cfg = json.loads(p[0])
		n1 = datetime.now()
		n1 = n1.replace(microsecond = 0)
		sys.stdout.flush()
		#if not c.execute('SELECT COUNT(*) FROM sqlite_master where type="table" and name="'+ PBT +'"').fetchone()[0] or c.execute('SELECT COUNT(*) FROM '+PBT+' WHERE STATUS!="OK" AND STATUS!=?',[str(cfg['epic_fail'])]).fetchall()[0][0] != 0:
		if cfg['TYPE'] == 'flickr':
			if halt == 1: return 
			cur_pub = SI_flickr(pub,dbdir)
			cur_pub.upload()
			cur_pub = None
		n2 = datetime.now()
		n2 = n2.replace(microsecond = 0)
		if verbose > 4: print PBT +"@"+ str(n2) + ":" +" Elapsed: "+ str((n2 - n1))+ ":"+ str(c.execute('SELECT COUNT(PK), STATUS FROM '+PBT+' GROUP BY STATUS').fetchall())
#		print PBT +" Elapsed: "+ str((n2 - n1) - timedelta(microseconds=(n2 - n1).microseconds))


def sighandler(s1,s2):
	global halt
	global cur_pub
	
	if s1 < 16:
		print "Received Signal " + str(s1) + str(s2)
		if cur_pub != None:
			cur_pub.halt = 1
		halt = 1

for i in [x for x in dir(signal) if x.startswith("SIG")]:
	try:
		signum = getattr(signal,i)
		signal.signal(signum,sighandler)
		if verbose > 4: print('Signal Handler Registered for ' + i)
	except Exception, e:
		if verbose > 4: print('Signal Handler Not Registered for ' + i)
	
#MAIN LOOP
while halt == 0:
	gen_tables()
	if halt == 1: quit()
	read_exifjson()
	if halt == 1: quit()
	run_pubs()
	if halt == 1: quit()
	time.sleep(60)
	
conn.close()