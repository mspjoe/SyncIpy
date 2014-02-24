#!/usr/bin/python
import sqlite3, glob, json, sys, re, inspect, flickr_api, subprocess,os
from datetime import datetime

class SI_flickr(object):

	#GLOBALS
	def __init__(self,publisher_id=None,tdbdir = None, vb=4):
		if tdbdir == None:
			self.dbdir = "."
		else:
			self.dbdir = tdbdir
			
		if publisher_id == None:
			self.pub = 0
		else:
			self.pub = publisher_id
		
		self.verbose=vb
		
		PBT = "PB"+str(self.pub)
		self.halt = 0

		#if self.halt == 1: return

		#OPEN SQL
		self.conn = sqlite3.connect(os.path.join(self.dbdir,'SyncIpy.db'))
		c  = self.conn.cursor()
		c2 = self.conn.cursor()
		c3 = self.conn.cursor()
		
		if self.verbose > 4: print('Loading config and settigns for '+str(PBT))
		c.execute('SELECT CONFIG FROM PUBLISHERS WHERE ID=?',[self.pub])
		self.cfg = json.loads(c.fetchone()[0])
		c.execute('SELECT FILE FROM PUBLISHERS WHERE ID=?',[self.pub])
		cfg_file = str(c.fetchone()[0])
		self.sys_args = {'is_public':self.cfg['is_public'], 'is_family':self.cfg['is_family'],'is_friend':self.cfg['is_friend'],'hidden':self.cfg['hidden'],'safety_level':self.cfg['safety_level'],'async':self.cfg['async']}
		epicfail = self.cfg['epic_fail']
		

		if self.verbose > 4: print('Creating if not exists table '+str(PBT))
		c.execute(	'CREATE TABLE IF NOT EXISTS ' +PBT+ ' ('+
					'"ID" INTEGER PRIMARY KEY AUTOINCREMENT,' +
					'"PK" TEXT,'  		+
					'"SK" TEXT,'  	 	+
					'"STATUS" TEXT,'   	+
					'"E_PATH" TEXT,'	+
					'"UDTTM" TEXT)')
		if self.verbose > 4: print('Creating if not exists indexes id1_'+PBT+' and id2_'+PBT+' for '+PBT)
		c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id1_'+PBT+' on '+PBT+' (PK ASC)')
		c.execute('CREATE UNIQUE INDEX IF NOT EXISTS id2_'+PBT+' on '+PBT+' (SK ASC)')
		
	 	for photo in c.execute('SELECT PK,PATH,FILE FROM PHOTOS WHERE STATUS="OK" AND PK NOT IN (SELECT PK FROM '+PBT+') AND PATH LIKE ?',[self.cfg['PATH']+"%"]):
			if self.verbose > 3: print('Inserting into '+PBT+' '+photo[1]+'/'+photo[2] + ' | ' + photo[0] )
			c2.execute('INSERT INTO '+PBT+' (PK,SK,STATUS) VALUES (?,?,?)',[photo[0],"TEMPSK_"+photo[0],"NW"])
		self.conn.commit()
		

		if self.verbose > 4: 
			print "Create: ", c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS="NW"').fetchone()[0]
			print "Change: ", c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS="XO"').fetchone()[0]
			print "Retry:  ", c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS!="XO" AND '+PBT+'.STATUS!="NW" AND '+PBT+'.STATUS!="OK" AND '+PBT+'.STATUS!=?',[epicfail]).fetchone()[0]
			print "Fail:   ", c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS=?',[epicfail]).fetchone()[0]
			print "Remove: ", c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"').fetchone()[0]			
			print "Total:  ",c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"').fetchone()[0] + c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS!="OK" AND '+PBT+'.STATUS!=?',[epicfail]).fetchone()[0]
		
		if 1 > c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"').fetchone()[0] + c.execute('SELECT COUNT('+PBT+'.PK) FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="OK" AND '+PBT+'.STATUS!="OK" AND '+PBT+'.STATUS!=?',[epicfail]).fetchone()[0]:		
			if self.verbose > 4: print "Quitting Flickr Publisher Plugin.  Nothing to do."
			self.halt = 1
		
		if self.halt == 1: return
		
		
		
		flickr_api.set_keys(api_key = str(self.cfg['api_key']), api_secret = str(self.cfg['secret']))

		try:
			flickr_api.set_auth_handler(cfg_file+'.oa')
			if self.verbose > 4: print('Loaded oauth for flickr on'+str(PBT))
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
				print 				
		try:	
			user = flickr_api.test.login()
		except Exception, e:
			self.halt = 1
			if self.verbose > 0: print "flickr_api.test.login() :" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)


	def exif_match(self,s,exifdata): 
	# Finds all strings within brackets in s.
	# Searches for those strings in dictionary exifdata
	# Replaces those strings with what it finds.
	# Replaces misses with nothing.
	# Selects the first non whitespace part as separated by pipes.
	# ie....  	s 	= "  [SUPERDUPER] | [Applesauce] [Hotdog]" 
	# 	 exifdata 	= "["SUPERDUPE":"12345","AppleSauce":"532355","Hotdog":"Yay!"]
	#		return 	= "  Yay!"
		for tg in re.findall("\[(.*?)\]",s):
			try:
				s = s.replace("["+tg+"]",str(exifdata[tg]))
			except:
				s = s.replace("["+tg+"]","")
		for t_ps in s.split('|'):
			if t_ps.strip()!="":
				return t_ps


	def upload(self):
		if self.halt == 1: return
		epicfail = self.cfg['epic_fail']
		user = flickr_api.test.login()
		PBT = "PB"+str(self.pub)
		c = self.conn.cursor()
		c2 = self.conn.cursor()
		
		c.execute('SELECT '+PBT+'.ID,'+PBT+'.PK,'+PBT+'.SK, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE FROM '+PBT+', PHOTOS WHERE PHOTOS.PK = '+PBT+'.PK AND PHOTOS.STATUS="RM"')
		for tc in c:
			
			id,pk,sk,uddtm,path,file = tc
			
			if uddtm != None:
				try:
					if self.verbose > 2: print('Deleting Missing Photo '+ file + ' from ' + str(user))	
					fp = flickr_api.Photo(id=sk)
					fp.delete()
				
				
				except Exception, e:
					if self.verbose > 0: print file + ":" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)
			
			if self.verbose > 2: print('Deleting Missing Photo '+ file + ' from DB')
			c2.execute('DELETE FROM '+PBT+' WHERE ID=?',[id])


		self.conn.commit()
		if self.halt == 1: return
					
		if self.verbose > 4: print('Reading Photosets For ' + str(user))
		photosets = user.getPhotosets()
		sys.stdout.flush()
		
		if self.halt == 1: return
		
		c.execute('SELECT PK FROM '+ PBT + ' WHERE STATUS!="OK"')
		for tc in c:
			if self.halt == 1: return
			c2.execute('SELECT '+PBT+'.PK, '+PBT+'.SK, '+PBT+'.STATUS, '+PBT+'.UDTTM, PHOTOS.PATH, PHOTOS.FILE ,PHOTOS.EXIF FROM '+PBT+ ', PHOTOS WHERE PHOTOS.PK='+PBT+'.PK AND PHOTOS.PK="'+tc[0]+'"')
			pk,sk,status,udttm,path,file,raw_exif = c2.fetchone()
			pj = os.path.join(path,file)
			arg_list = self.sys_args.copy()
			arg_list['photo_file'] = pj
			exif = json.loads(raw_exif)
			if self.verbose > 2: print('Photo '+ str(pj))
			fp = None

			if self.cfg['description'] !="":
				arg_list['description'] = self.exif_match(self.cfg['description'],exif) 
			if self.cfg['title'] !="":
				arg_list['title'] = self.exif_match(self.cfg['title'],exif) 
			if self.cfg['tags'] !="":
				arg_list['tags'] = self.exif_match(self.cfg['tags'],exif) 
			tdttm = datetime.now()
		
			
			if udttm != None and status != "OK" and status != epicfail and status != "UL" and status != "DL":
				if self.verbose > 2: print('... Deleting Existing '+ str(sk) + ' from ' + str(user))
				try:
					
					fp = flickr_api.Photo(id=sk)
					fp.delete()
					status = "DL"	
					udttm = None
					photosets = user.getPhotosets()

				except Exception, e:
					if self.verbose > 0: print file + ":" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)
					status = epicfail
			

			if status != epicfail and status != "OK":
				if status != "UL":
					if self.verbose > 2: print('... Uploading to ' + str(user))
					try:				
						fp = flickr_api.upload( **arg_list )
						sk = fp['id']
						status = "UL"
						udttm = tdttm
					except Exception, e: 
						if self.verbose > 0: print file + ":" +  '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)
						if status.isdigit():
							dstatus = float(status)
							if dstatus > float(epicfail):
								status = epicfail
							else:
								status = str(float(status)+1)
						else:
							status = "1"

				try:
					if fp == None and status == 'UL':
						fp = flickr_api.Photo(id=sk)
				except Exception, e:
					if self.verbose > 0: print file + ":" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)

				try:
					if fp != None and status == 'UL':
						
						dttm= datetime.strptime(exif[self.cfg['date_posted']],'%Y:%m:%d %H:%M:%S')
						udttm = int(( dttm - datetime(1970,1,1)).total_seconds())
						if udttm < self.cfg['min_date_posted']:
							udttm = self.cfg['min_date_posted'] + (udttm / 35000)
						fp.setDates(date_posted=str(udttm))
						if self.verbose > 2: print('... Setting Date to ' + str(udttm))

				except Exception, e:
					if self.verbose > 0: print file + ":" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)

				try:
					if status == 'UL' and 'Keywords' in exif and fp != None:
						kws = exif['Keywords']
						if isinstance(kws, basestring):
							kws = [kws]
						for kw in (kws):
							psk = None
							for ps in photosets:
								if str(ps['title']) == str(kw):
									psk = ps
							if psk == None:
								photoset = flickr_api.Photoset.create(title = str(kw), primary_photo = fp)
								if self.verbose > 2: print('... Creating Photoset ' + str(kw))
								photosets = user.getPhotosets()

							else:
								try:
									psk.addPhoto(photo = fp)
									if self.verbose > 2: print('... Adding to Photoset ' + str(kw))
								except Exception, e:
									if self.verbose > 0: print file + ":" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)

					status = "OK"
				except Exception, e:
					if self.verbose > 0: print file + ":" + '{0.filename}-L{0.lineno}:'.format(inspect.getframeinfo(inspect.currentframe())) + str(e)
		
			c2.execute('UPDATE '+PBT+' SET STATUS=?, SK=?, UDTTM=? WHERE PK=?',[status,sk,udttm,pk])
			self.conn.commit()
			if self.verbose > 2: print('... Updated DB')
			sys.stdout.flush()


