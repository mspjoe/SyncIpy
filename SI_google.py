#!/usr/bin/python
import yaml, gdata.photos.service, gdata.media, sqlite3, glob, json, sys, re, inspect, subprocess,os,logging,time, atom, atom.service
from datetime import datetime
from PIL import Image

class SI_google(object):
		
	def findAlbum(self, title):
		if self.albums == None:
			self.albums = self.pws.GetUserFeed()
		for album in self.albums.entry:
			if album.title.text == title:
				self.log.info("Found Album " + title)
				return album
		return None

	def createAlbum(self, title):
		self.albums = None
		self.log.info( "Creating album " + title)
		# public, private, protected. private == "anyone with link"
		album = self.pws.InsertAlbum(title=title, summary='', access='private')
		return album

	def findOrCreateAlbum(self, title):
		delay = 1
		while True:
			try:
				album = self.findAlbum(title)
				if not album:
					album = self.createAlbum(title)
				return album
			except gdata.photos.service.GooglePhotosException, e:
				self.log.exception( "caught exception " + str(e))
				self.log.exception("sleeping for " + str(delay) + " seconds")
				time.sleep(delay)
				delay = delay * 2
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
		self.albums = None

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
		for key in json.loads(SI_google_cfg()).keys():
			if not key in self.cfg:
				if missing_cfg == None:
					missing_cfg = "Please ensure the following configuration values exist in "+file+":"
				for line in SI_google_cfg().splitlines():
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
		self.sys_args = {'is_public':self.cfg['is_public']}
		epicfail = self.cfg['epic_fail']
		

		self.log.info('Creating if not exists table '+str(PBT))
		c.execute(  'CREATE TABLE IF NOT EXISTS ' +PBT+ ' ('+
					'"ID" INTEGER PRIMARY KEY AUTOINCREMENT,' +
					'"PK" TEXT,'		+
					'"SK" TEXT,'		+
					'"STATUS" TEXT,'	+
					'"SYNCED" TEXT,'	+
					'"UDTTM" TEXT)')
		self.log.info('Creating if not exists indexes id1_'+PBT+' and id2_'+PBT+' for '+PBT)
		c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id1_'+PBT+' on '+PBT+' (PK ASC)')
		c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id2_'+PBT+' on '+PBT+' (SK ASC)')

		if self.halt == 1: return

		gdata.photos.service.SUPPORTED_UPLOAD_TYPES = ('bmp', 'jpeg', 'jpg', 'gif', 'png', 'mov', 'mpg', 'mpeg')
		self.pws = gdata.photos.service.PhotosService()
		self.pws.ssl = False
		self.pws.email = "account@gmail.com"

		if (not os.path.isfile(cfg_file + '.oa')):
			with open(cfg_file + '.oa', "w") as oa_file:
				yaml.dump({"account": ["account@gmail.com", None]}, oa_file)

		try:
			with open(cfg_file + '.oa', "r") as oa_file:
				config = yaml.load(oa_file)
				user_account = config['account'][0]
				token = config['account'][1]
				self.pws.email = user_account

			self.pws.SetOAuthToken(token)
			self.pws.GetUserFeed(kind='album', user='default', limit=1)

		except Exception, e:
			self.pws.SetOAuthInputParameters(gdata.auth.OAuthSignatureMethod.HMAC_SHA1, consumer_key='anonymous', consumer_secret='anonymous')
			display_name = 'SyncIpy'
			fetch_params = {'xoauth_displayname':display_name}
			scopes = list(gdata.service.lookup_scopes('lh2'))
		  
			try:
				request_token = self.pws.FetchOAuthRequestToken(scopes=scopes, extra_parameters=fetch_params)
			except gdata.service.FetchingOAuthRequestTokenFailed, err:
				self.log.exception( err[0]['body'].strip() + '; Request token retrieval failed!')
				self.halt = 1
		  
			auth_params = {'hd': self.cfg['domain']}
			auth_url = self.pws.GenerateOAuthAuthorizationURL(request_token=request_token, extra_params=auth_params)
			message = 'Please log in and/or grant access via your browser at ' + auth_url + ' then hit enter.'
			raw_input(message)

			# This upgrades the token, and if successful, sets the access token
			try:
				self.pws.UpgradeToOAuthAccessToken(request_token)
			except gdata.service.TokenUpgradeFailed, e:
				self.log.exception('Token upgrade failed! Could not get OAuth access token.')
				self.log.exception(str(e))
				self.halt = 1
		
		if self.halt == 1: 
			self.log.exception('Failed to request access')
			return
		
		config['account'][1] = self.pws.current_token
		with open(cfg_file + '.oa', "w") as oa_file:
			yaml.dump(config, oa_file)
		
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
				c.execute(  'SELECT '+PBT+'.PK,'+PBT+'.SK, '+PBT+'.STATUS, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE ,PHOTOS.EXIF,PHOTOS.MDTTM FROM '+PBT+ 
						', PHOTOS WHERE PHOTOS.PK='+PBT+'.PK AND PHOTOS.PK=?',[kwargs['pk']])

			elif 'sk' in kwargs:
				c.execute(  'SELECT '+PBT+'.PK,'+PBT+'.SK, '+PBT+'.STATUS, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE ,PHOTOS.EXIF,PHOTOS.MDTTM FROM '+PBT+ 
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
	# ie....	s   = "  [SUPERDUPER] | [Applesauce] [Hotdog]" 
	#	exifdata   = "["SUPERDUPE":"12345","AppleZauce":"532355","Hotdog":"Yay!"]
	###	 return  = "  Yay!"
	

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

		
		

	def getContentType(self,filename):
		knownExtensions = {
		'.png': 'image/png',
		'.jpeg': 'image/jpeg',
		'.jpg': 'image/jpeg',
		'.avi': 'video/avi',
		'.wmv': 'video/wmv',
		'.3gp': 'video/3gp',
		'.m4v': 'video/m4v',
		'.mp4': 'video/mp4',
		'.mov': 'video/mov'
		}

		ext = os.path.splitext(filename)[1].lower()
		if ext in knownExtensions:
			return knownExtensions[ext]
		else:
			return None

	

		

	def shrinkIfNeeded(self, path):
		img = Image.open(path)
		if max(img.size) > self.cfg['max_photo_size']:
			self.log.info( "Shrinking " + path + " to " + str(self.cfg['max_photo_size']))
			imagePath = os.path.join(self.cfg['temp_dir'], os.path.basename(path))
		
			img = Image.open(path)
			(w,h) = img.size
			if (w>h):
				img2 = img.resize((self.cfg['max_photo_size'], (h*self.cfg['max_photo_size'])/w), Image.ANTIALIAS)
			else:
				img2 = img.resize(((w*self.cfg['max_photo_size'])/h, self.cfg['max_photo_size']), Image.ANTIALIAS)
			img2.save(imagePath, 'JPEG', quality=99)
			cmd = ['exiftool', '-TagsFromFile', path,'--Orientation', '--ImageSize', imagePath]
			dn = open(os.devnull,"w")
			out = subprocess.check_output(cmd,stderr=dn)
			dn.close()
			
			return imagePath
		return path
		
	# Uploads os.path.join(path,file) to Google and returns google photo id. 
	# If that fails, returns 0. 
	# If not enough arguments are passed, returns -1.  path + file + exif will be most efficient.
	def upload_photo(self,**kwargs):
		if not 'path' in kwargs or not 'file' in kwargs or not 'exif' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'path' in kwargs or not 'file' in kwargs or not 'exif' in kwargs: return -1
		try:
			if self.cfg['album_mode'] == "T" and self.cfg['album_tags'] in kwargs['exif']:
				album_name = kwargs['exif'][self.cfg['album_tags']]
			elif self.cfg['album_mode'] == "D" and self.cfg['album_dttm_tag'] in kwargs['exif']:
				album_name =  datetime.strptime( kwargs['exif'][self.cfg['album_dttm_tag']], "%Y:%m:%d %H:%M:%S").strftime(self.cfg['album_dttm_format'])
			else:
				album_name = self.cfg['album_static']

			album_suf = 1

			localPath = os.path.join(kwargs['path'],kwargs['file'])
			contentType = self.getContentType(localPath)

			if contentType.startswith('image/'):
				imagePath = self.shrinkIfNeeded(localPath)
				isImage = True
				picasa_photo = gdata.photos.PhotoEntry()
			else:
				size = os.path.getsize(localPath)

				# tested by cpbotha on 2013-05-24
				# this limit still exists
				if size > self.cfg['max_vid_bytes']:
					self.log.exception( "Video file too big to upload: " + str(size) + " > " + str(self.cfg['max_vid_bytes']))
					return 0
				imagePath = localPath
				isImage = False
				picasa_photo = VideoEntry()
			picasa_photo.title = atom.Title(text=kwargs['file'])
			picasa_photo.summary = atom.Summary(text='', summary_type='text')
			delay = 1
			upload = None
			while True:
				if album_suf > 1:
					album = self.findOrCreateAlbum(album_name + " #" + str(album_suf))
				else:
					album = self.findOrCreateAlbum(album_name) 
				if self.halt == 1: 
					break
				try:
					self.log.info(self.current_file+": Uploading To Google")
					if isImage:
						upload = self.pws.InsertPhoto(album, picasa_photo, imagePath, content_type=contentType)
					else:
						upload = self.pws.InsertVideo(album, picasa_photo, imagePath, content_type=contentType)
					break
				except gdata.photos.service.GooglePhotosException, e:
					if e[2] == 'Photo limit reached.':
						album_suf = album_suf + 1

				  	self.log.exception(str(e))
				  	self.log.exception("retrying in " + str(delay) + " seconds")
				  	time.sleep(delay)
				  	delay = delay * 2

		# delete the temp file that was created if we shrank an image:
			if imagePath != localPath:
				os.remove(imagePath)
			if upload <> None:
				return upload.GetEditLink().href
			
		except Exception, e: 
			self.log.exception(str(e))
			return 0

	# Deletes photo with google service ID of sk. Returns 1 if no longer on google.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  sk is most efficient. 
	def delete_photo(self,**kwargs):
		if not 'sk' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'sk' in kwargs: return -1

		try:
			self.pws.Delete(self.pws.GetEntry(kwargs['sk']))
		except Exception, e:
			if type(e) ==  gdata.photos.service.GooglePhotosException  and e[0] == 404:
				self.log.info(self.current_file+": File Already Deleted on Google.  Continuing")
			else:
				self.log.info(self.current_file+ ": " + str(e))
				return 0
		return 1
		
	# Replaces photo with google service ID of sk. Returns sk of photo on google.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  sk + path + file is most efficient.	
	def replace_photo(self,**kwargs):
		if not 'file' in kwargs or not 'path' in kwargs or not 'sk' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'file' in kwargs or not 'path' in kwargs or not 'sk' in kwargs: return -1
		
		self.log.info(kwargs['file'] + ": Replacing on Google")
		
		rv = self.delete_photo(**kwargs)
		if rv < 1: 
			return rv
		else:
			return self.upload_photo(**kwargs)
		#Need to replace above if else with blob functionality... or not.  

		try:

			localPath = os.path.join(kwargs['path'],kwargs['file'])
			contentType = self.getContentType(localPath)

			if contentType.startswith('image/'):
				imagePath = self.shrinkIfNeeded(localPath)
				isImage = True
			else:
				size = os.path.getsize(localPath)

				# tested by cpbotha on 2013-05-24
				# this limit still exists
				if size > self.cfg['max_vid_bytes']:
					self.log.exception( "Video file too big to upload: " + str(size) + " > " + str(self.cfg['max_vid_bytes']))
					return 0 
				imagePath = localPath
				isImage = False

			delay = 1
			upload = None
			while True:
				if self.halt == 1: 
					break
				try:
					if isImage:
						upload = self.pws.InsertPhoto(self.stream_album, self.pws.GetEntry(kwargs['sk']), imagePath, content_type=contentType)
					else:
						upload = self.pws.InsertVideo(self.stream_album, self.pws.GetEntry(kwargs['sk']), imagePath, content_type=contentType)
					break
				except gdata.photos.service.GooglePhotosException, e:
				  self.log.exception(str(e))
				  self.log.exception("retrying in " + str(delay) + " seconds")
				  time.sleep(delay)
				  delay = delay * 2

		# delete the temp file that was created if we shrank an image:
			if imagePath != localPath:
				os.remove(imagePath)
			if upload <> None:
				return upload.GetEditLink().href
			
		except Exception, e: 
			self.log.exception(str(e))
			return 0


	# Sets photo metadata with configured tags. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  sk + exif is most efficient 
	def set_photo_metadata(self,**kwargs):

		if not 'exif' in kwargs or not 'sk' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'exif' in kwargs or not 'sk' in kwargs: return -1
	
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
			else: 		   tags = tags + ', "'+ tag + '"'
		try: 
			p = self.pws.GetEntry(kwargs['sk'])
			title = self.exif_match(self.cfg['title'],kwargs['exif']) 
			if title == "":
				title = kwargs['file']
			p.title.text = title
			p.summary.text = self.exif_match(self.cfg['description'],kwargs['exif']) 
			p.media.keywords.text = tags
			self.pws.UpdatePhotoMetadata(p)
		except Exception, e:
			self.log.exception(self.current_file + ': ' +str(e))
			return 0
		return 1

	# Sets photo photosets with configured tags. Returns 1 if succesful.
	# If that fails, returns 0. 
	# If not enough arguments are specified to function, returns -1.  sk + exif is most efficient
	# Will Revisit If New Functionality Is Released Google
	def set_photo_photosets(self, **kwargs):

		return 1
		import pdb
		pdb.set_trace()

		if not 'exif' in kwargs or not 'sk' in kwargs: kwargs = self.extend_kwargs(**kwargs)
		if not 'exif' in kwargs or not 'sk' in kwargs: return -1

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




	def sync(self):
		if self.halt == 1: return
		PBT = "PB"+str(self.pub)
		c  = self.conn.cursor()
		c2 = self.conn.cursor()

		epicfail = self.cfg['epic_fail']
		
		for photo in c.execute( 'SELECT '+PBT+'.PK,'+PBT+'.SK,PHOTOS.FILE FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"'):
			if self.halt != 1:
				pk,sk,self.current_file = photo
				 
				self.log.info(self.current_file + ": Deleting From Google")
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

			if udttm < mdttm or synced_raw == None or synced_raw == '' or synced_raw == '{"I": 1, "M": 1, "A": 1}':
				synced = {'I':0,'A':0,'M':0}
			else:
				synced = json.loads(synced_raw)

			self.log.info(self.current_file + ": Sync Starting With Status " + str(synced))

			if sk == "TEMPSK_" + pk:
				synced['I'] = 0

			if synced['I'] == 1 or udttm != None:

				try:
					fp = self.pws.GetEntry(sk)
				except Exception, e:
					if type(e) ==  gdata.photos.service.GooglePhotosException and e[0] == 404:
						fp = None
						synced['I'] = 0
					else:
						self.log.info(self.current_file+ ": " + str(e))

			try:
				udttm =  os.path.getmtime(os.path.join(path,file))
			except Exception, e:
				pass

			if fp != None:
				rv = self.replace_photo(sk=sk, file=file, path=path, exif=exif)
			elif synced['I'] == 0:
				rv = self.upload_photo(sk=sk, file=file, path=path, exif=exif)
			else:
				rv = sk
			
			if rv < 1 :
				status = status + 1
				fp = None
			elif rv == 1:
				rv = sk
				synced['I'] = 1
			elif rv != 1:
				synced['I'] = 1
				try:
					sk = self.pws.GetEntry(rv).GetEditLink().href
					fp = self.pws.GetEntry(rv).GetEditLink().href
				except Exception, e:
					fp = None
					status = status + 1
					self.log.info(self.current_file+ ": " + str(e))

			if fp != None:
				if synced['M'] != 1: synced['M'] = self.set_photo_metadata(sk=sk,exif=exif,file=file)
				if synced['A'] != 1: synced['A'] = self.set_photo_photosets(sk=sk, exif=exif)
				sk = self.pws.GetEntry(rv).GetEditLink().href

				
			synced_raw = json.dumps(synced)

			if sum(synced.values()) == len(synced):
				status = 'OK'

			self.log.info(self.current_file + ": Sync Ending With Status " + str(synced))
			
			c2.execute('UPDATE '+PBT+' SET STATUS=?, SK=?, UDTTM=?, SYNCED=? WHERE PK=?',[status,sk,udttm,synced_raw,pk])
			self.conn.commit()
			

def SI_google_cfg():
	return ''' {

	"PATH": "Directory to be monitored",
	"PATH": "/Users/Joe/Desktop/Temp/test",

	"EXT": "Allowed Extensions Allowed Separated by Pipes",
	"EXT": "jpg|JPG|png|PNG",
	
	"TYPE": "This JSON format is specific to the Google plugin",
	"TYPE": "google",

	"ENABLED": "If set to true, this will be read and active for syncing", 
	"ENABLED": false, 

	"temp_dir": "Directory Used to Resize Media",
	"temp_dir": "/tmp/",

	"title":"Exiftool tag names within brackets are replaced and string is split along pipe.  First non null split is used.",
	"title":"[Headline]|[FileName]",

	"description":"Exiftool tag names within brackets are replaced and string is split along pipe.  First non null split is used.",
	"description":"[Caption-Abstract]",

	"tags":"Exiftool tag names are are used.",
	"tags":"Keywords,City,Province-State,Country-Primary Location Name",

	"album_mode":"Mode for album naming.  Can be one of three modes.  (T) A certain tag specified in album tags; (D) the create date as formatted by strftime; or (S) a static named stream album.",
	"album_mode":"D",

	"album_dttm_format":"Date format used by python strftime",
	"album_dttm_format":"%Y",

	"album_dttm_tag":"Date exif tag used",
	"album_dttm_tag":"DateTimeOriginal",

	"album_static": "Album where all photos are added.  Will increment as needed.", 
	"album_static": "Photostream",

	"album_tags":"Exiftool tag names are are used to generate Albums if google supported them.  As for now, this does nothing.",
	"album_tags":"Keywords",

	"album_tags_to_ignore":"Pipe Delimited List of values for which the photosets will not be added to or created",
	"album_tags_to_ignore":"",

	"is_public":"Set to 1 to default permission to public.  Else set to 0",
	"is_public":0,

	"perm_comment":"who can add comments to the photo and it's notes. one of 0: the owner, 1: friends & family, 2: contacts, 3: everybody",
	"perm_comment":0,

	"max_photo_size":"Set to max photo size to upload.",
	"max_photo_size":2048,

	"max_vid_bytes":"Set to max photo size to upload.",
	"max_vid_bytes":104857600,

	"epic_fail":"Set to the maximum failures before giving up during an upload",
	"epic_fail":5,

	"domain":"Domain to use for auth.  Should probably just leave alone but I think this will work for other sites.  Cannot test myself.",
	"domain":"default"


 }'''
