#!/usr/bin/python
import sqlite3, glob, json, sys, inspect, subprocess, hashlib, time, os, signal
from datetime import datetime,timedelta
from SI_flickr import *
import logging


#GLOBALS
plugins = 	[
				["flickr","SI_flickr",SI_flickr_cfg()]
	    	]
			
#The following is the default config diretory.  If passed from the 
cfgdir = os.path.join(os.getenv('HOME'),".SyncIpy")
dbdir = os.path.join(os.getenv('HOME'),".SyncIpy")
sleep_seconds = 60

pubs = []
halt = 0
cur_pub = None
cfg = {}

#Init Log
formatter = logging.Formatter(fmt='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
log = logging.getLogger('SyncIpy')
log.addHandler(handler)

default_config=''' {
	"dbdir": "Directory for database to be stored",
	"dbdir": "'''+str(dbdir)+'''",

	"log_level": "Verbosity of log level output.  Choose from: CRITICAL, ERROR, WARNING, INFO, DEBUG",
	"log_level": "CRITICAL",

	"sleep_seconds": "Delay from end of last sync to start of next sync in seconds",
	"sleep_seconds": '''+str(sleep_seconds)+'''
 }'''

if len(sys.argv) == 2:
	cfgdir = sys.argv[0]
elif len(sys.argv) > 2:
	log.critical("Too many arguments provided.  A single path where config files can be found is the only argument")
	quit()

#Create Config Dir and Read Config if Available.  Add example config files to directory.
if not os.path.isdir(cfgdir):
	try:
		log.info("Config Dir " + cfgdir + "does not exists.  Creating directory default config and sample pub configs.")
		os.mkdir(cfgdir)
		f = open(os.path.join(cfgdir,"SyncIpy.cfg.sample"),"w")
		f.write(default_config)
		f.close()
		for plugin in plugins:
			f = open(os.path.join(cfgdir,plugin[0]+".pub.sample"),"w")
			f.write(plugin[2])
			f.close()
	except Exception, e:
		log.critical("Config Dir " + cfgdir + "does not exists and user does not have sufficiant permission to create it")
		log.exception(str(e))
		quit()
else:
	try:
		cfg = json.load(open(os.path.join(cfgdir,'SyncIpy.cfg')))
	except Exception, e:
		log.critical("Problems Reading " + os.path.join(cfgdir,'SyncIpy.cfg') +"\nPlease ensure file is created and is properly formatted" )
		log.exception(str(e))
		quit()

missing_cfg = None
for key in json.loads(default_config).keys():
	if not key in cfg :
		if missing_cfg == None:
			missing_cfg = "Please ensure the following configuration values exist in SyncIpy.cfg:"
		for line in default_config.splitlines():
			if line.strip().startswith('"'+key):
				missing_cfg = missing_cfg + '\n' + line
		missing_cfg = missing_cfg + '\n'

if missing_cfg != None: 
	log.critical( missing_cfg)
	quit()
	
try:
	if   cfg['log_level'] == "CRITICAL": log.setLevel(logging.CRITICAL)
	elif cfg['log_level'] == "ERROR":	 log.setLevel(logging.ERROR)
	elif cfg['log_level'] == "WARN": 	 log.setLevel(logging.WARN)
	elif cfg['log_level'] == "INFO": 	 log.setLevel(logging.INFO)
	elif cfg['log_level'] == "DEBUG": 	 log.setLevel(logging.DEBUG)
	dbdir = str(cfg['dbdir'])
	sleep_seconds = int(cfg['sleep_seconds'])
except Exception, e:
	log.exception("Error reading SyncIpy.cfg config settings: " + str(e))

	
#OPEN SQL
conn = sqlite3.connect(os.path.join(dbdir,'SyncIpy.db'))
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

	for config_f in glob.glob(cfgdir+"/*.pub"):
		try:
			j = json.load(open(config_f))
		except Exception, e: 
			j = {'TYPE':'ConfigError', 'ENABLED':False}
			log.critical( config_f + " was unable to be parsed.  Please review the config file for accuracy.")
			log.exception(str(e))

		c.execute('INSERT OR IGNORE INTO PUBLISHERS (FILE) VALUES(?)',[config_f])
		c.execute('UPDATE PUBLISHERS SET TYPE=?, ENABLED=?, CONFIG=? WHERE FILE=?',[j['TYPE'],j['ENABLED'],json.dumps(j),config_f])
		if j['ENABLED'] == True: 	  
			tID = c.execute('SELECT ID FROM PUBLISHERS WHERE FILE=?',[config_f]).fetchone()[0]
			pubs.append(tID)
			
	conn.commit()



#DIR SCANNING FUNCTIONS
def read_exifjson():
	j = []
	pub_dir = {}
	modified_images = set()
	missing_images = set()
	mdttms = {}

	c.execute('SELECT PATH,FILE,MDTTM FROM PHOTOS')
	for photo in c:
		mdttms[os.path.join(photo[0],photo[1])]=photo[2]
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
				if (pj not in mdttms or int(mdttms[pj]) != int(mdttm)) and mdttm < utcnowdttm:
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
			log.error( i["FileName"] + ":" + "exiftool reported error" + i['Error'])
			prehash = 'ERROR:'+pj
			sts='ER'
		elif 'OriginalDocumentID' in i:
			try:
				prehash=i["OriginalDocumentID"]+":"+pj
				
			except Exception, e: 
				log.exception( i["FileName"] + ": "  + str(e))
		else:
			print i["FileName"] + ":" + "no OriginalDocumentID tag present.  Using NONE" 
			prehash = 'NONE:'+pj
		id = str(hashlib.sha256(prehash).hexdigest()) 
		c.execute('REPLACE INTO PHOTOS (PK, PATH, FILE, STATUS, MDTTM, EXIF) VALUES (?,?,?,?,?,?)',[id,i['Directory'],i['FileName'],sts,dt,json.dumps(i)])
		log.info( i["FileName"] + ": Replacing Entry in DB Table Photos")
		if sts == 'OK':
			for pub in pubs:
				PBT = "PB"+str(pub)
				if c.execute('SELECT COUNT(*) FROM sqlite_master where type="table" and name="'+ PBT +'"').fetchone()[0] and pj.startswith(pub_dir[PBT]):
					c.execute('UPDATE OR IGNORE '+PBT+' SET STATUS="XO" WHERE PK=?',[id])
					c.execute('INSERT OR IGNORE INTO '+PBT+' (PK,SK,STATUS) VALUES (?,?,?)',[id,"TEMPSK_"+id,"NW"])
					log.info( i["FileName"] + ": Updating Entry in DB Table " + PBT)
	conn.commit()
	return

def run_pubs():
	global cur_pub
	log.info("Current Photo Status in Photos DB Table: "+str(c.execute('SELECT COUNT(PK),STATUS from PHOTOS GROUP BY STATUS').fetchall()))
	for pub in pubs:
		cur_pub = None
		if halt == 1: return 
		PBT= "PB"+str(pub)
		p = (c.execute('SELECT CONFIG,FILE FROM PUBLISHERS WHERE ID=?',[pub]).fetchall()[0])
		cfg = json.loads(p[0])
		n1 = datetime.now()
		n1 = n1.replace(microsecond = 0)
		sys.stdout.flush()
		
		if halt == 1: return 
		try:
			for plugin in plugins:
				if cfg['TYPE'] == plugin[0]:
					cur_pub = globals()[plugin[1]](pub,dbdir)
		except Exception, e:
			print e
		if cur_pub == None:
			"Plugin Type "+ cfg['TYPE']+" Invalid in Config " + p[1]
		else:
			cur_pub.sync()
			cur_pub = None
		#conn = sqlite3.connect(os.path.join(dbdir,'SyncIpy.db'))
		n2 = datetime.now()
		n2 = n2.replace(microsecond = 0)
		log.info( PBT + ":" +" Elapsed: "+ str((n2 - n1))+ ":"+ str(c.execute('SELECT COUNT(PK), STATUS FROM '+PBT+' GROUP BY STATUS').fetchall()))



def sighandler(s1,s2):
	global halt
	global cur_pub

	if s1 < 16:
		log.info( "Received Signal " + str(s1) + "["+str(s2)+"]")
		if cur_pub != None:
			cur_pub.halt = 1
		halt = 1


# Main Executable Starts Here

for i in [x for x in dir(signal) if x.startswith("SIG")]:
	try:
		signum = getattr(signal,i)
		signal.signal(signum,sighandler)
		log.debug('Signal Handler Registered for ' + i)
	except Exception, e:
		log.debug('Signal Handler Not Registered for ' + i)
	


#MAIN LOOP
gen_tables()
while halt == 0 and len(pubs) > 0:
	gen_tables()
	if halt == 1: quit()
	read_exifjson()
	if halt == 1: quit()
	run_pubs()
	if halt == 1: quit()
	time.sleep(sleep_seconds)
	
conn.close()