#!/usr/bin/python
import sqlite3, glob, json, sys, re, inspect, flickr_api, subprocess,os
from datetime import datetime
import logging

class SI_flickr(object):
	
	#GLOBALS
	def __init__(self,publisher_id=None,tdbdir = None):

		if tdbdir == None:
			self.dbdir = "."
		else:
			self.dbdir = tdbdir

		if publisher_id == None:
			self.pub = 0
		else:
			self.pub = publisher_id

		self.log = logging.getLogger('SyncIpy')
		PBT = "PB"+str(self.pub)
		self.halt = 0
		self.photosets = None

		#OPEN SQL
		self.conn = sqlite3.connect(os.path.join(self.dbdir,'SyncIpy.db'))
		c  = self.conn.cursor()
		c2 = self.conn.cursor()
		c3 = self.conn.cursor()

		self.log.info('Loading config and settigns for '+str(PBT))
		c.execute('SELECT CONFIG,FILE FROM PUBLISHERS WHERE ID=?',[self.pub])

		self.cfg, file = c.fetchone()
		self.cfg = json.loads(self.cfg)

		missing_cfg = None
		self.log.debug('Checking for missing keys in config file')
		for key in json.loads(SI_flickr_cfg()).keys():
			if not key in self.cfg:
				if missing_cfg == None:
					missing_cfg = "Please ensure the following configuration values exist in "+file+":"
				for line in SI_flickr_cfg().splitlines():
					if line.strip().startswith('"'+key):
						missing_cfg = missing_cfg + '\n' + line
				self.halt = 1
				missing_cfg = missing_cfg + '\n'


		if missing_cfg != None: 
			self.log.critical( missing_cfg)
			return

		if self.halt == 1: return

		c.execute('SELECT FILE FROM PUBLISHERS WHERE ID=?',[self.pub])
		cfg_file = str(c.fetchone()[0])
		self.sys_args = {'is_public':self.cfg['is_public'], 'is_family':self.cfg['is_family'],'is_friend':self.cfg['is_friend'],'hidden':self.cfg['hidden'],'safety_level':self.cfg['safety_level'],'async':self.cfg['async']}
		epicfail = self.cfg['epic_fail']


		self.log.info('Creating if not exists table '+str(PBT))
		c.execute(	'CREATE TABLE IF NOT EXISTS ' +PBT+ ' ('+
					'"ID" INTEGER PRIMARY KEY AUTOINCREMENT,' +
					'"PK" TEXT,'  		+
					'"SK" TEXT,'  	 	+
					'"STATUS" TEXT,'   	+
					'"SYNCED" TEXT,'	+
					'"UDTTM" TEXT)')
		self.log.info('Creating if not exists indexes id1_'+PBT+' and id2_'+PBT+' for '+PBT)
		c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id1_'+PBT+' on '+PBT+' (PK ASC)')
		c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id2_'+PBT+' on '+PBT+' (SK ASC)')

		if self.halt == 1: return

		flickr_api.set_keys(api_key = str(self.cfg['api_key']), api_secret = str(self.cfg['secret']))

		try:
			flickr_api.set_auth_handler(cfg_file+'.oa')
			self.log.info('Loaded oauth for flickr on '+str(PBT))
		except:

			try:
				a = flickr_api.auth.AuthHandler()
				perms = "delete"
				print 'Could not validate oath tolken.  Please visit the following URL'
				print  a.get_authorization_url(perms)
				print 'After visiting. Please paste the verifier code:'
				vf = raw_input()
				a.set_verifier(vf.strip())
				a.save(cfg_file+'.oa')
				flickr_api.set_auth_handler(a)
			except Exception, e:
				self.log.exception(str(e))
		try:	
			user = flickr_api.test.login()
		except Exception, e:
			self.halt = 1
			self.log.exception(str(e))

	### This function helps keep SQL calls out of many other functions
	# It does so by returning all possible data for a photo based on a few different identifiers.
	# It will attempt to fill out the kwargs and pass them back with more data than they started with.  
	# This allows functions to keep on trucking despite lazy calling.  Also... nice for python prompt.
	# Returns either starting kwargs if not enough info or:
	### PHOTOS.PK, PHOTOS.SK, PUB.STATUS, PUB.UDTTM, PHOTOS.PATH, PHOTOS.FILE, PHOTOS.EXIF into pk,sk,status,uddtm,path,file,exif
	def extend_kwargs(self,**kwargs):
		self.log.info('extend_kwargs called')
		PBT = "PB"+str(self.pub)
		c = self.conn.cursor()
		try:
			if 'pk' in kwargs:
				c.execute(	'SELECT '+PBT+'.PK,'+PBT+'.SK, '+PBT+'.STATUS, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE ,PHOTOS.EXIF,PHOTOS.MDTTM FROM '+PBT+ 
						', PHOTOS WHERE PHOTOS.PK='+PBT+'.PK AND PHOTOS.PK=?',[kwargs['pk']])

			elif 'sk' in kwargs:
				c.execute(	'SELECT '+PBT+'.PK,'+PBT+'.SK, '+PBT+'.STATUS, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE ,PHOTOS.EXIF,PHOTOS.MDTTM FROM '+PBT+ 
						', PHOTOS WHERE PHOTOS.PK='+PBT+'.PK AND '+PBT+'.SK=?',[kwargs['sk']])
			else:
				return kwargs
		except Exception, e:			
			self.log.exception(str(e))
			return kwargs
	
		kwargs['pk'],kwargs['sk'],kwargs['status'],kwargs['udttm'],kwargs['path'],kwargs['file'],raw_exif,kwargs['mdttm'] = c.fetchone()
		kwargs['exif'] = json.loads(raw_exif)
		return kwargs
				

	### Finds all strings within brackets in s.
	# Searches for those strings in dictionary exifdata
	# Replaces those strings with what it finds.
	# Replaces misses with nothing.
	# Selects the first non whitespace part as separated by pipes.
	# ie....  	s 	= "  [SUPERDUPER] | [Applesauce] [Hotdog]" 
	# 	 exifdata 	= "["SUPERDUPE":"12345","AppleZauce":"532355","Hotdog":"Yay!"]
	###		return 	= "  Yay!"
	

	def exif_match(self,s,exifdata): 
		if s == "":
			return s
		for tg in re.findall("\[(.*?)\]",s):
			try:
				s = s.replace("["+tg+"]",str(exifdata[tg]))
			except:
				s = s.replace("["+tg+"]","")
		for t_ps in s.split('|'):
			if t_ps.strip()!="":
				return t_ps

	# Prints photo status counts
	def status():
		PBT = "PB"+str(self.pub)
		c = self.conn.cursor()
		c2 = self.conn.cursor()
		self.log.info( "Create: " + str(c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS="NW"').fetchone()[0]))
		self.log.info( "Change: " + str(c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS="XO"').fetchone()[0]))
		self.log.info( "Retry:  " + str(c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS!="XO" AND '+PBT+'.STATUS!="NW" AND '+PBT+'.STATUS!="OK" AND '+PBT+'.STATUS!=?',[epicfail]).fetchone()[0]))
		self.log.info( "Fail:   " + str(c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS=?',[epicfail]).fetchone()[0]))
		self.log.info( "Remove: " + str(c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"').fetchone()[0]))
		self.log.info( "Total:  " + str(c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"').fetchone()[0] + c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS!="OK" AND '+PBT+'.STATUS!=?',[epicfail]).fetchone()[0]))

		
	# Uploads os.path.join(path,file) to flickr and returns flicker photo id. 
	# If that fails, returns 0. 
	# If not enough arguments are passed, returns -1.  path + file will be most efficient.
	def upload_photo(self,**kwargs):
		if not 'path' in kwargs or not 'file' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'path' in kwargs or not 'file' in kwargs: return -1
		try:
			self.log.info(self.current_file+": Uploading To Flickr")
			fp = flickr_api.upload(photo_file = os.path.join(kwargs['path'],kwargs['file']))
			return fp['id']
		except Exception, e: 
			self.log.exception(str(e))
			return 0

	# Deletes photo with flicker service ID of fp/sk. Returns 1 if no longer on flickr.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk is most efficient. 
	def delete_photo(self,**kwargs):

		if not 'sk' in kwargs and not 'fp' in kwargs: kwargs = extend_kwargs(**kwargs)
		if not 'sk' in kwargs and not 'fp' in kwargs: return -1

		try:
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])

			self.log.info(self.current_file+": Deleting From Flickr")
			fp.delete()
		except Exception, e:

			if type(e) ==  flickr_api.flickrerrors.FlickrAPIError and e.code == 1:
				self.log.info(self.current_file+": File Already Deleted on Flickr.  Continuing")
			else:
				self.log.info(self.current_file+ ": " + str(e))
				return 0
		self.photosets = None
		return 1
		
	# Replaces photo with flicker service ID of fp/sk. Returns sk of photo on flickr.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk + path + file is most efficient. 	
	def replace_photo(self,**kwargs):
		if not 'file' in kwargs or not 'path' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): kwargs = extend_kwargs(**kwargs)
		if not 'file' in kwargs or not 'path' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): return -1
		
		self.log.info(kwargs['file'] + ": Replacing on Flickr")
		rv = self.delete_photo(**kwargs)
		if rv < 1: 
			return rv
		else:
			return self.upload_photo(**kwargs)
			


	# Sets photo upload dttm with configured tag. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk + exif is most efficient 
	def set_photo_date(self,**kwargs):
		
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): kwargs = self.extend_kwargs(**kwargs)
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): return -1

		try:
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])

			dttm= datetime.strptime(kwargs['exif'][self.cfg['date_posted']],'%Y:%m:%d %H:%M:%S')
			udttm = int(( dttm - datetime(1970,1,1)).total_seconds())
			if udttm < self.cfg['min_date_posted']:
				udttm = self.cfg['min_date_posted'] + (udttm / 35000)
			fp.setDates(date_posted=str(udttm))
			self.log.info(self.current_file+": Setting Date to " + str(udttm))

		except Exception, e:
			self.log.exeception(self.current_file + ': ' +str(e))
			return 0
		return 1


	# Sets photo metadata with configured tags. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk + exif is most efficient 
	def set_photo_metadata(self,**kwargs):

		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): kwargs = self.extend_kwargs(**kwargs)
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): return -1

		arg_list = {}
		
		arg_list['description'] = self.exif_match(self.cfg['description'],kwargs['exif']) 
		arg_list['title'] = self.exif_match(self.cfg['title'],kwargs['exif']) 

		try: 
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])
			fp.setMeta(**arg_list)
		except Exception, e:
			self.log.exeception(self.current_file + ': ' +str(e))
			return 0
		return 1

	# Sets photo permissions with configured values. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk is most efficient 
	def set_photo_perms(self,**kwargs):

		if not 'sk' in kwargs and not 'fp' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'sk' in kwargs and not 'fp' in kwargs: return -1

		try: 
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])
			fp.setPerms(is_public=self.cfg['is_public'],is_friend=self.cfg['is_friend'],is_family=self.cfg['is_family'],perm_comment=self.cfg['perm_comment'],perm_addmeta=self.cfg['perm_addmeta'])
		except Exception, e:
			self.log.exeception(self.current_file + ': ' +str(e))
			return 0
		return 1

	# Sets photo safety with configured values. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk is most efficient 
	def set_photo_safety(self,**kwargs):
		if not 'sk' in kwargs and not 'fp' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'sk' in kwargs and not 'fp' in kwargs: return -1


		try: 
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])

			fp.setSafetyLevel(hidden=self.cfg['hidden'],safety_level=self.cfg['safety_level'])
		except Exception, e:
			self.log.exeception(self.current_file + ': ' +str(e))
			return 0
		return 1


	# Sets photo tags with configured tags. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk + exif is most efficient 
	def set_photo_tags(self,**kwargs):
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): kwargs = self.extend_kwargs(**kwargs)
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): return -1
		
		tag_set = []

		for tag_source in self.cfg['tags'].split(','):
			if tag_source in kwargs['exif']:
				tag_value = kwargs['exif'][tag_source]
				if isinstance(tag_value, basestring):
					tag_set.append(tag_value)
				else:
					tag_set.extend(tag_value)
		tags = ""
		for tag in set(tag_set):
			if tags == '': tags = '"'+tag+'"'
			else:				tags = tags + ', "'+ tag + '"'
		
		self.log.info(self.current_file + ': Setting Tags To ' + tags)
		try:
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])
			fp.setTags(tags)
		except Exception, e:
			self.log.exeception(self.current_file + ': ' +str(e))
			return 0
		return 1
	

	# Sets photo photosets with configured tags. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  fp/sk + exif is most efficient
	def set_photo_photosets(self, **kwargs):
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): kwargs = self.extend_kwargs(**kwargs)
		if not 'exif' in kwargs or (not 'sk' in kwargs and not 'fp' in kwargs): return -1

		desired_ps = []
		try:
			tags_to_ignore = self.cfg['photoset_names_to_ignore'].split("|")
		except Exception,e:
			tags_to_ignore = []

		try:
			photoset_tags = self.cfg['photoset_tags'].split(",")
		except Exception,e:
			photoset_tags  = []


		for tag in photoset_tags:
			if not tag in tags_to_ignore and tag in kwargs['exif']:

				tag_value = kwargs['exif'][tag]
				if isinstance(tag_value, basestring):
					desired_ps.append(tag_value)
				else:
					desired_ps.extend(tag_value)

		current_ps = []

		try:
			if 'fp' in kwargs:
				fp = kwargs['fp']
			else:
				fp = flickr_api.Photo(id=kwargs['sk'])
			for ppps in fp.getAllContexts()[0]:
				if type(ppps) == flickr_api.objects.Photoset:
					if ppps.title in desired_ps:
						current_ps.append(ppps.title)
					else:
						self.log.info(self.current_file + ': Removing Photo From Photoset ' + ppps.title + ' [' + ppps.id +']')
						try:
							ppps.removePhoto(fp)
						except Exception, e:
							self.log.exeception(self.current_file + ': ' +str(e))
							return 0

			for dps in desired_ps:
				if not dps in current_ps:
					psk = None
					if self.photosets == None:
						self.log.debug(self.current_file + ': Reading Photosets From Flickr')
						self.photosets = flickr_api.test.login().getPhotosets()
					for ps in self.photosets:
						if str(ps['title']) == str(dps):
							psk = ps
					if psk == None:
						photoset = flickr_api.Photoset.create(title = str(dps), primary_photo = fp)
						self.log.info(self.current_file + ': Creating Photoset ' + str(dps))
						self.photosets = None
					else:
						try:
							psk.addPhoto(photo = fp)
							self.log.info(self.current_file + ': Adding to Photoset ' + str(dps))
						except Exception, e:
							self.log.exeception(self.current_file + ': ' +str(e))
							return 0
		except Exception, e:
			self.log.exeception(self.current_file + ': ' +str(e))
			return 0
		return 1




	def sync(self):
		if self.halt == 1: return
		PBT = "PB"+str(self.pub)
		c  = self.conn.cursor()
		c2 = self.conn.cursor()

		epicfail = self.cfg['epic_fail']
		
		for photo in c.execute(	'SELECT '+PBT+'.PK,'+PBT+'.SK,PHOTOS.FILE FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"'):
			if self.halt != 1:
				pk,sk,self.current_file = photo
				 
				self.log.info(self.current_file + ": Deleting From Flickr")
				if self.delete_photo(sk=sk) == 1:
					c2.execute('DELETE FROM '+PBT+' WHERE pk=?',[pk])
					self.log.info(self.current_file + ": Deleting From DB Table " + PBT)
		self.conn.commit()

		for photo in c.execute('SELECT PK,PATH,FILE FROM PHOTOS WHERE STATUS="OK" AND PK NOT IN (SELECT PK FROM '+PBT+') AND PATH LIKE ?',[self.cfg['PATH']+"%"]):
			pk,path,self.current_file = photo
			self.log.info(self.current_file + ": Inserting as NW into " + PBT)
			c2.execute('INSERT INTO '+PBT+' (PK,SK,STATUS) VALUES (?,?,?)',[photo[0],"TEMPSK_"+photo[0],"NW"])
		self.conn.commit()

		for tc in c.execute('SELECT PK FROM '+ PBT + ' WHERE STATUS!="OK" AND STATUS!=?',[epicfail]):
			if self.halt == 1: return
			c2.execute('SELECT '+PBT+'.PK,'+PBT+'.SK, '+PBT+'.STATUS, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE ,PHOTOS.EXIF,PHOTOS.MDTTM,'+PBT+'.SYNCED FROM '+PBT+ 
					', PHOTOS WHERE PHOTOS.PK='+PBT+'.PK AND PHOTOS.PK=?',[tc[0]])
			
			pk, sk, status, udttm, path,file,raw_exif,mdttm,synced_raw = c2.fetchone()
			
			exif = json.loads(raw_exif)
			self.current_file = str(file)
			try:
				status = int(status)
			except Exception:
				status = 0
			
			fp = None

			if udttm < mdttm or synced_raw == None or synced_raw == '' or synced_raw == '{"D": 1, "F": 1, "I": 1, "M": 1, "P": 1, "S": 1, "T": 1}':
				synced = {'I':0,'S':0,'F':0,'P':0,'T':0,'M':0,'D':0}
			else:
				synced = json.loads(synced_raw)

			self.log.info(self.current_file + ": Sync Starting With Status " + str(synced))

			if synced['I'] == 1 or udttm != None:
				try:
					fp = flickr_api.Photo(id=sk)
				except Exception, e:
					if type(e) ==  flickr_api.flickrerrors.FlickrAPIError and e.code == 1:
						fp = None
						synced['I'] = 0
					else:
						self.log.info(self.current_file+ ": " + str(e))

			try:
				udttm =  os.path.getmtime(os.path.join(path,file))
			except Exception, e:
				pass

			if fp != None:
				rv = self.replace_photo(fp=fp, file=file, path=path)
			elif synced['I'] == 0:
				rv = self.upload_photo(sk=sk, file=file, path=path)
			
			if rv < 1 :
				status = status + 1
				fp = None
			elif rv == 1:
				rv = sk
				synced['I'] = 1
			elif rv != 1:
				synced['I'] = 1
				try:
					sk = rv
					fp = flickr_api.Photo(id=sk)
				except Exception, e:
					fp = None
					status = status + 1
					self.log.info(self.current_file+ ": " + str(e))

			if fp != None:
				
				if synced['S'] != 1: synced['S'] = self.set_photo_photosets(fp=fp, sk=sk, exif=exif)
				if synced['T'] != 1: synced['T'] = self.set_photo_tags(fp=fp, sk=sk, exif=exif)
				if synced['F'] != 1: synced['F'] = self.set_photo_safety(fp=fp,sk=sk)
				if synced['P'] != 1: synced['P'] = self.set_photo_perms(fp=fp,sk=sk)
				if synced['M'] != 1: synced['M'] = self.set_photo_metadata(fp=fp,sk=sk,exif=exif)
				if synced['D'] != 1: synced['D'] = self.set_photo_date(fp=fp,sk=sk,exif=exif)
				
			synced_raw = json.dumps(synced)

			if sum(synced.values()) == len(synced):
				status = 'OK'

			self.log.info(self.current_file + ": Sync Ending With Status " + str(synced))
			
			c2.execute('UPDATE '+PBT+' SET STATUS=?, SK=?, UDTTM=?, SYNCED=? WHERE PK=?',[status,sk,udttm,synced_raw,pk])
			self.conn.commit()
			


def SI_flickr_cfg():
	return ''' {

	"PATH": "Directory to be monitored",
	"PATH": "/Users/Joe/Desktop/Temp/test",

	"EXT": "Allowed Extensions Allowed Separated by Pipes",
	"EXT": "jpg|JPG|png|PNG",
	
	"TYPE": "This JSON format is specific to the Flickr plugin",
	"TYPE": "flickr",

	"ENABLED": "If set to true, this will be read and active for syncing", 
	"ENABLED": true, 

	"api_key": "Do Not Change", 
	"api_key": "496109f8cbd00b85e12548ac0fe71699", 

	"secret": "Do Not Change", 
	"secret": "e9547c04cbca60a5", 

	"title":"Exiftool tag names within brackets are replaced and string is split along pipe.  First non null split is used.",
	"title":"[Headline]",

	"description":"Exiftool tag names within brackets are replaced and string is split along pipe.  First non null split is used.",
	"description":"[Caption-Abstract]",

	"tags":"Exiftool tag names are are used.",
	"tags":"Keywords,City,Province-State,Country-Primary Location Name",

	"photoset_tags":"Exiftool tag names are are used to generate Photosets.",
	"photoset_tags":"",

	"photoset_names_to_ignore":"Pipe Delimited List of values for which the photosets will not be added to or created",
	"photoset_names_to_ignore":"",

	"is_public":"Set to 1 to default permission to public.  Else set to 0",
	"is_public":0,

	"is_family":"Set to 1 to default permission to family.  Else set to 0",
	"is_family":0,

	"is_friend":"Set to 1 to default permission to friend.  Else set to 0",
	"is_friend":0,

	"hidden":"Set to 1 to keep the photo in global search results, 2 to hide from public searches.",
	"hidden":0,
	
	"perm_comment":"who can add comments to the photo and it's notes. one of 0: the owner, 1: friends & family, 2: contacts, 3: everybody",
	"perm_comment":0,

	"perm_addmeta":"who can add notes and tags to the photo. one of 0: the owner, 1: friends & family, 2: contacts, 3: everybody",
	"perm_addmeta":0,

	"safety_level":"Set to 1 for Safe, 2 for Moderate, or 3 for Restricted.",
	"safety_level":1,

	"async":"Set to 1 for async mode, 0 for sync mode",
	"async":0,
	
	"epic_fail":"Set to the maximum failures before giving up during an upload",
	"epic_fail":5,
	
	"date_posted":"If blank, flickr decides.  Otherwise select exiftool date fields to use in prefered order",
	"date_posted":"DateTimeOriginal",
	
	"min_date_posted":"If using date_posted parameter.  Enter minumum unix timestamp here to send to flickr.  This date has to be after your flickr join date.  Dates prior will be spread out in dttm sequence over the date set in this parameters",
	"min_date_posted":1203897600

 }'''
