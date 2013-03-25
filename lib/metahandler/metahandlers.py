'''
    These classes cache metadata from TheMovieDB and TVDB.
    It uses sqlite databases.
       
    It uses themoviedb JSON api class and TVDB XML api class.
    For TVDB it currently uses a modified version of 
    Python API by James Smith (http://loopj.com)
    
    Metahandler intially created for IceFilms addon, reworked to be it's own 
    script module to be used by many addons.

    Created/Modified by: Eldorado
    
    Initial creation and credits: Daledude / Anarchintosh / WestCoast13 
    
    
*To-Do:
- write a clean database function (correct imgs_prepacked by checking if the images actually exist)
  for pre-packed container creator. also retry any downloads that failed.
  also, if  database has just been created for pre-packed container, purge all images are not referenced in database.

'''

import os
import re
import sys
from datetime import datetime
import time
from TMDB import TMDB
from thetvdbapi import TheTVDB

#necessary so that the metacontainers.py can use the scrapers
import xbmc

''' Use t0mm0's common library for http calls '''
from t0mm0.common.addon import Addon
from t0mm0.common.net import Net
net = Net()

addon = Addon('script.module.metahandler')
addon_path = addon.get_path()
sys.path.append((os.path.split(addon_path))[0])

'''
   Use SQLIte3 wherever possible, needed for newer versions of XBMC
   Keep pysqlite2 for legacy support
'''
try:
    if  addon.get_setting('use_remote_db')=='true' and   \
        addon.get_setting('db_address') is not None and  \
        addon.get_setting('db_user') is not None and     \
        addon.get_setting('db_pass') is not None and     \
        addon.get_setting('db_name') is not None:
        import mysql.connector as database
        addon.log('Loading MySQLdb as DB engine', 2)
        DB = 'mysql'
    else:
        raise ValueError('MySQL not enabled or not setup correctly')
except:
    try: 
        from sqlite3 import dbapi2 as database
        addon.log('Loading sqlite3 as DB engine', 2)
    except: 
        from pysqlite2 import dbapi2 as database
        addon.log('pysqlite2 as DB engine', 2)
    DB = 'sqlite'


def make_dir(mypath, dirname):
    ''' Creates sub-directories if they are not found. '''
    subpath = os.path.join(mypath, dirname)
    if not os.path.exists(subpath): os.makedirs(subpath)
    return subpath


def bool2string(myinput):
    ''' Neatens up usage of preparezip flag. '''
    if myinput is False: return 'false'
    elif myinput is True: return 'true'

        
class MetaData:  
    '''
    This class performs all the handling of meta data, requesting, storing and sending back to calling application

        - Create cache DB if it does not exist
        - Create a meta data zip file container to share
        - Get the meta data from TMDB/IMDB/TVDB
        - Store/Retrieve meta from cache DB
        - Download image files locally
    '''  

     
    def __init__(self, path='special://profile/addon_data/script.module.metahandler/', preparezip=False):

        #Check if a path has been set in the addon settings
        settings_path = addon.get_setting('meta_folder_location')
        
        if settings_path:
            self.path = xbmc.translatePath(settings_path)
        else:
            self.path = xbmc.translatePath(path)
        
        self.cache_path = make_dir(self.path, 'meta_cache')

        if preparezip:
            #create container working directory
            #!!!!!Must be matched to workdir in metacontainers.py create_container()
            self.work_path = make_dir(self.path, 'work')
            
        #set movie/tvshow constants
        self.type_movie = 'movie'
        self.type_tvshow = 'tvshow'
        self.type_season = 'season'        
        self.type_episode = 'episode'
            
        #this init auto-constructs necessary folder hierarchies.

        # control whether class is being used to prepare pre-packaged .zip
        self.classmode = bool2string(preparezip)
        self.videocache = os.path.join(self.cache_path, 'video_cache.db')

        self.tvpath = make_dir(self.cache_path, self.type_tvshow)
        self.tvcovers = make_dir(self.tvpath, 'covers')
        self.tvbackdrops = make_dir(self.tvpath, 'backdrops')
        self.tvbanners = make_dir(self.tvpath, 'banners')

        self.mvpath = make_dir(self.cache_path, self.type_movie)
        self.mvcovers = make_dir(self.mvpath, 'covers')
        self.mvbackdrops = make_dir(self.mvpath, 'backdrops')

        # connect to db at class init and use it globally
        if DB == 'mysql':
            class MySQLCursorDict(database.cursor.MySQLCursor):
                def _row_to_python(self, rowdata, desc=None):
                    row = super(MySQLCursorDict, self)._row_to_python(rowdata, desc)
                    if row:
                        return dict(zip(self.column_names, row))
                    return None
            db_address = addon.get_setting('db_address')
            db_port = addon.get_setting('db_port')
            if db_port: db_address = '%s:%s' %(db_address,db_port)
            db_user = addon.get_setting('db_user')
            db_pass = addon.get_setting('db_pass')
            db_name = addon.get_setting('db_name')
            self.dbcon = database.connect(db_name, db_user, db_pass, db_address, buffered=True)
            self.dbcur = self.dbcon.cursor(cursor_class=MySQLCursorDict, buffered=True)
        else:
            self.dbcon = database.connect(self.videocache)
            self.dbcon.row_factory = database.Row # return results indexed by field names and not numbers so we can convert to dict
            self.dbcur = self.dbcon.cursor()


        # !!!!!!!! TEMPORARY CODE !!!!!!!!!!!!!!!
        if os.path.exists(self.videocache):
            table_exists = True
            try:
                sql_select = 'select * from tvshow_meta'
                self.dbcur.execute(sql_select)
                matchedrow = self.dbcur.fetchall()[0]
            except:
                table_exists = False

            if table_exists:
                sql_select = 'SELECT year FROM tvshow_meta'
                if DB == 'mysql':
                    sql_alter = 'RENAME TABLE tvshow_meta TO tmp_tvshow_meta'
                else:
                    sql_alter = 'ALTER TABLE tvshow_meta RENAME TO tmp_tvshow_meta'
                try:
                    self.dbcur.execute(sql_select)
                    matchedrow = self.dbcur.fetchall()[0]
                except Exception, e:
                    print '************* tvshow year column does not exist - creating temp table'
                    print e
                    self.dbcur.execute(sql_alter)
                    self.dbcon.commit()
    
        ## !!!!!!!!!!!!!!!!!!!!!!!


        # initialize cache db
        self._cache_create_movie_db()

        
        # !!!!!!!! TEMPORARY CODE !!!!!!!!!!!!!!!
        
        if DB == 'mysql':
            sql_insert = "INSERT INTO tvshow_meta (imdb_id, tvdb_id, title, year, cast, rating, duration, plot, mpaa, premiered, genre, studio, status, banner_url, cover_url, trailer_url, backdrop_url, imgs_prepacked, overlay) SELECT imdb_id, tvdb_id, title, cast(substr(premiered, 1,4) as unsigned) as year, cast, rating, duration, plot, mpaa, premiered, genre, studio, status, banner_url, cover_url, trailer_url, backdrop_url, imgs_prepacked, overlay FROM tmp_tvshow_meta"
        else:
            sql_insert = "INSERT INTO tvshow_meta (imdb_id, tvdb_id, title, year, cast, rating, duration, plot, mpaa, premiered, genre, studio, status, banner_url, cover_url, trailer_url, backdrop_url, imgs_prepacked, overlay) SELECT imdb_id, tvdb_id, title, cast(substr(premiered, 1,4) as integer) as year, [cast], rating, duration, plot, mpaa, premiered, genre, studio, status, banner_url, cover_url, trailer_url, backdrop_url, imgs_prepacked, overlay FROM tmp_tvshow_meta"
        sql_select = 'SELECT imdb_id from tmp_tvshow_meta'
        sql_drop = 'DROP TABLE tmp_tvshow_meta'
        try:
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchall()[0]
            self.dbcur.execute(sql_insert)
            self.dbcon.commit()
            self.dbcur.execute(sql_drop)
            self.dbcon.commit()
        except Exception, e:
            print '************* tmp_tvshow_meta does not exist: %s' % e

        ## !!!!!!!!!!!!!!!!!!!!!!!



    def __del__(self):
        ''' Cleanup db when object destroyed '''
        try:
            self.dbcur.close()
            self.dbcon.close()
        except: pass


    def _cache_create_movie_db(self):
        ''' Creates the cache tables if they do not exist.  '''   

        # Create Movie table
        sql_create = "CREATE TABLE IF NOT EXISTS movie_meta ("\
                           "imdb_id TEXT, "\
                           "tmdb_id TEXT, "\
                           "title TEXT, "\
                           "year INTEGER,"\
                           "director TEXT, "\
                           "writer TEXT, "\
                           "tagline TEXT, cast TEXT,"\
                           "rating FLOAT, "\
                           "votes TEXT, "\
                           "duration TEXT, "\
                           "plot TEXT,"\
                           "mpaa TEXT, "\
                           "premiered TEXT, "\
                           "genre TEXT, "\
                           "studio TEXT,"\
                           "thumb_url TEXT, "\
                           "cover_url TEXT, "\
                           "trailer_url TEXT, "\
                           "backdrop_url TEXT,"\
                           "imgs_prepacked TEXT,"\
                           "overlay INTEGER,"\
                           "UNIQUE(imdb_id, tmdb_id, title, year)"\
                           ");"
        if DB == 'mysql':
            sql_create = sql_create.replace("imdb_id TEXT","imdb_id VARCHAR(10)")
            sql_create = sql_create.replace("tmdb_id TEXT","tmdb_id VARCHAR(10)")
            sql_create = sql_create.replace("title TEXT"  ,"title VARCHAR(255)")
            self.dbcur.execute(sql_create)
            try: self.dbcur.execute('CREATE INDEX nameindex on movie_meta (title);')
            except: pass
        else:
            self.dbcur.execute(sql_create)
            self.dbcur.execute('CREATE INDEX IF NOT EXISTS nameindex on movie_meta (title);')
        addon.log('Table movie_meta initialized', 0)
        
        # Create TV Show table
        sql_create = "CREATE TABLE IF NOT EXISTS tvshow_meta ("\
                           "imdb_id TEXT, "\
                           "tvdb_id TEXT, "\
                           "title TEXT, "\
                           "year INTEGER,"\
                           "cast TEXT,"\
                           "rating FLOAT, "\
                           "duration TEXT, "\
                           "plot TEXT,"\
                           "mpaa TEXT, "\
                           "premiered TEXT, "\
                           "genre TEXT, "\
                           "studio TEXT,"\
                           "status TEXT,"\
                           "banner_url TEXT, "\
                           "cover_url TEXT,"\
                           "trailer_url TEXT, "\
                           "backdrop_url TEXT,"\
                           "imgs_prepacked TEXT,"\
                           "overlay INTEGER,"\
                           "UNIQUE(imdb_id, tvdb_id, title)"\
                           ");"

        if DB == 'mysql':
            sql_create = sql_create.replace("imdb_id TEXT","imdb_id VARCHAR(10)")
            sql_create = sql_create.replace("tvdb_id TEXT","tvdb_id VARCHAR(10)")
            sql_create = sql_create.replace("title TEXT"  ,"title VARCHAR(255)")
            self.dbcur.execute(sql_create)
            try: self.dbcur.execute('CREATE INDEX nameindex on tvshow_meta (title);')
            except: pass
        else:
            self.dbcur.execute(sql_create)
            self.dbcur.execute('CREATE INDEX IF NOT EXISTS nameindex on tvshow_meta (title);')
        addon.log('Table tvshow_meta initialized', 0)

        # Create Season table
        sql_create = "CREATE TABLE IF NOT EXISTS season_meta ("\
                           "imdb_id TEXT, "\
                           "tvdb_id TEXT, " \
                           "season INTEGER, "\
                           "cover_url TEXT,"\
                           "overlay INTEGER,"\
                           "UNIQUE(imdb_id, tvdb_id, season)"\
                           ");"
               
        if DB == 'mysql':
            sql_create = sql_create.replace("imdb_id TEXT","imdb_id VARCHAR(10)")
            sql_create = sql_create.replace("tvdb_id TEXT","tvdb_id VARCHAR(10)")
            self.dbcur.execute(sql_create)
        else:
            self.dbcur.execute(sql_create)
        addon.log('Table season_meta initialized', 0)
                
        # Create Episode table
        sql_create = "CREATE TABLE IF NOT EXISTS episode_meta ("\
                           "imdb_id TEXT, "\
                           "tvdb_id TEXT, "\
                           "episode_id TEXT, "\
                           "season INTEGER, "\
                           "episode INTEGER, "\
                           "title TEXT, "\
                           "director TEXT, "\
                           "writer TEXT, "\
                           "plot TEXT, "\
                           "rating FLOAT, "\
                           "premiered TEXT, "\
                           "poster TEXT, "\
                           "overlay INTEGER, "\
                           "UNIQUE(imdb_id, tvdb_id, episode_id, title)"\
                           ");"
        if DB == 'mysql':
            sql_create = sql_create.replace("imdb_id TEXT"   ,"imdb_id VARCHAR(10)")
            sql_create = sql_create.replace("tvdb_id TEXT"   ,"tvdb_id VARCHAR(10)")
            sql_create = sql_create.replace("episode_id TEXT","episode_id VARCHAR(10)")
            sql_create = sql_create.replace("title TEXT"     ,"title VARCHAR(255)")
            self.dbcur.execute(sql_create)
        else:
            self.dbcur.execute(sql_create)

        addon.log('Table episode_meta initialized', 0)

        # Create Addons table
        sql_create = "CREATE TABLE IF NOT EXISTS addons ("\
                           "addon_id TEXT, "\
                           "movie_covers TEXT, "\
                           "tv_covers TEXT, "\
                           "tv_banners TEXT, "\
                           "movie_backdrops TEXT, "\
                           "tv_backdrops TEXT, "\
                           "last_update TEXT, "\
                           "UNIQUE(addon_id)"\
                           ");"

        if DB == 'mysql':
            sql_create = sql_create.replace("addon_id TEXT", "addon_id VARCHAR(255)")
            self.dbcur.execute(sql_create)
        else:
            self.dbcur.execute(sql_create)
        addon.log('Table addons initialized', 0)


    def _init_movie_meta(self, imdb_id, tmdb_id, name, year=0):
        '''
        Initializes a movie_meta dictionary with default values, to ensure we always
        have all fields
        
        Args:
            imdb_id (str): IMDB ID
            tmdb_id (str): TMDB ID
            name (str): full name of movie you are searching
            year (int): 4 digit year
                        
        Returns:
            DICT in the structure of what is required to write to the DB
        '''                
        
        if year:
            int(year)
        else:
            year = 0
            
        meta = {}
        meta['imdb_id'] = imdb_id
        meta['tmdb_id'] = str(tmdb_id)
        meta['title'] = name
        meta['year'] = int(year)
        meta['writer'] = ''
        meta['director'] = ''
        meta['tagline'] = ''
        meta['cast'] = []
        meta['rating'] = 0
        meta['votes'] = ''
        meta['duration'] = ''
        meta['plot'] = ''
        meta['mpaa'] = ''
        meta['premiered'] = ''
        meta['trailer_url'] = ''
        meta['genre'] = ''
        meta['studio'] = ''
        
        #set whether that database row will be accompanied by pre-packed images.                        
        meta['imgs_prepacked'] = self.classmode
        
        meta['thumb_url'] = ''
        meta['cover_url'] = ''
        meta['backdrop_url'] = ''
        meta['overlay'] = 6
        return meta


    def _init_tvshow_meta(self, imdb_id, tvdb_id, name, year=0):
        '''
        Initializes a tvshow_meta dictionary with default values, to ensure we always
        have all fields
        
        Args:
            imdb_id (str): IMDB ID
            tvdb_id (str): TVDB ID
            name (str): full name of movie you are searching
            year (int): 4 digit year
                        
        Returns:
            DICT in the structure of what is required to write to the DB
        '''
        
        if year:
            int(year)
        else:
            year = 0
            
        meta = {}
        meta['imdb_id'] = imdb_id
        meta['tvdb_id'] = tvdb_id
        meta['title'] = name
        meta['TVShowTitle'] = name
        meta['rating'] = 0
        meta['duration'] = ''
        meta['plot'] = ''
        meta['mpaa'] = ''
        meta['premiered'] = ''
        meta['year'] = int(year)
        meta['trailer_url'] = ''
        meta['genre'] = ''
        meta['studio'] = ''
        meta['status'] = ''        
        meta['cast'] = []
        meta['banner_url'] = ''
        
        #set whether that database row will be accompanied by pre-packed images.
        meta['imgs_prepacked'] = self.classmode
        
        meta['cover_url'] = ''
        meta['backdrop_url'] = ''
        meta['overlay'] = 6
        meta['episode'] = 0
        meta['playcount'] = 0
        return meta


    def __init_episode_meta(self, imdb_id, tvdb_id, episode_title, season, episode, air_date):
        '''
        Initializes a movie_meta dictionary with default values, to ensure we always
        have all fields
        
        Args:
            imdb_id (str): IMDB ID
            tvdb_id (str): TVDB ID
            episode_title (str): full name of Episode you are searching - NOT TV Show name
            season (int): episode season number
            episode (int): episode number
            air_date (str): air date (premiered data) of episode - YYYY-MM-DD
                        
        Returns:
            DICT in the structure of what is required to write to the DB
        '''

        meta = {}
        meta['imdb_id']=imdb_id
        meta['tvdb_id']=''
        meta['episode_id'] = ''                
        meta['season']= int(season)
        meta['episode']= int(episode)
        meta['title']= episode_title
        meta['director'] = ''
        meta['writer'] = ''
        meta['plot'] = ''
        meta['rating'] = 0
        meta['premiered'] = air_date
        meta['poster'] = ''
        meta['cover_url']= ''
        meta['trailer_url']=''
        meta['premiered'] = ''
        meta['backdrop_url'] = ''
        meta['overlay'] = 6


    def _string_compare(self, s1, s2):
        """ Method that takes two strings and returns True or False, based
            on if they are equal, regardless of case.
        """
        try:
            return s1.lower() == s2.lower()
        except AttributeError:
            addon.log("Please only pass strings into this method.", 4)
            addon.log("You passed a %s and %s" % (s1.__class__, s2.__class__), 4)


    def _clean_string(self, string):
        """ 
            Method that takes a string and returns it cleaned of any special characters
            in order to do proper string comparisons
        """        
        try:
            return ''.join(e for e in string if e.isalnum())
        except:
            return string


    def _convert_date(self, string, in_format, out_format):
        ''' Helper method to convert a string date to a given format '''
        
        #Legacy check, Python 2.4 does not have strptime attribute, instroduced in 2.5
        if hasattr(datetime, 'strptime'):
            strptime = datetime.strptime
        else:
            strptime = lambda date_string, format: datetime(*(time.strptime(date_string, format)[0:6]))
        
        #strptime = lambda date_string, format: datetime(*(time.strptime(date_string, format)[0:6]))
        try:
            a = strptime(string, in_format).strftime(out_format)
        except Exception, e:
            addon.log('************* Error Date conversion failed: %s' % e, 4)
            return None
        return a


    def _downloadimages(self, url, path, name):
        '''
        Download images to save locally
        
        Args:
            url (str): picture url
            path (str): destination path
            name (str): filename
        '''                 

        if not os.path.exists(path):
            os.makedirs(path)
        
        full_path = os.path.join(path, name)
        self._dl_code(url, full_path)              


    def _picname(self,url):
        '''
        Get image name from url (ie my_movie_poster.jpg)      
        
        Args:
            url (str): full url of image                        
        Returns:
            picname (str) representing image name from file
        '''           
        picname = re.split('\/+', url)
        return picname[-1]
         
        
    def _dl_code(self,url,mypath):
        '''
        Downloads images to store locally       
        
        Args:
            url (str): url of image to download
            mypath (str): local path to save image to
        '''        
        addon.log('Attempting to download image from url: %s ' % url, 0)
        addon.log('Saving to destination: %s ' % mypath, 0)
        if url.startswith('http://'):
          
            try:
                 data = net.http_GET(url).content
                 fh = open(mypath, 'wb')
                 fh.write(data)  
                 fh.close()
            except Exception, e:
                addon.log('Image download failed: %s ' % e, 4)
        else:
            if url is not None:
                addon.log('Not a valid url: %s ' % url, 4)


    def _valid_imdb_id(self, imdb_id):
        '''
        Check and return a valid IMDB ID    
        
        Args:
            imdb_id (str): IMDB ID
        Returns:
            imdb_id (str) if valid with leading tt, else None
        '''      
        # add the tt if not found. integer aware.       
        if not imdb_id.startswith('tt'):
            imdb_id = 'tt%s' % imdb_id
        if re.search('tt[0-9]{7}', imdb_id):
            return imdb_id
        else:
            return None


    def _remove_none_values(self, meta):
        ''' Ensure we are not sending back any None values, XBMC doesn't like them '''
        for item in meta:
            if meta[item] is None:
                meta[item] = ''            
        return meta


    def __insert_from_dict(self, table, size):
        ''' Create a SQL Insert statement with dictionary values '''
        sql = 'INSERT INTO %s ' % table
        
        if DB == 'mysql':
            format = ', '.join(['%s'] * size)
        else:
            format = ', '.join('?' * size)
        
        sql_insert = sql + 'Values (%s)' % format
        return sql_insert


    def __set_playcount(self, overlay):
        '''
        Quick function to check overlay and set playcount
        Playcount info label is required to have > 0 in order for watched flag to display in Frodo
        '''
        if int(overlay) == 7:
            return 1
        else:
            return 0


    def check_meta_installed(self, addon_id):
        '''
        Check if a meta data pack has been installed for a specific addon

        Queries the 'addons' table, if a matching row is found then we can assume the pack has been installed
        
        Args:
            addon_id (str): unique name/id to identify an addon
                        
        Returns:
            matchedrow (dict) : matched row from addon table
        '''

        if addon_id:
            sql_select = "SELECT * FROM addons WHERE addon_id = '%s'" % addon_id
        else:
            addon.log('Invalid addon id', 3)
            return False
        
        addon.log('Looking up in local cache for addon id: %s' % addon_id, 2)
        addon.log('SQL Select: %s' % sql_select, 0)
        try:    
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()            
        except Exception, e:
            addon.log('************* Error selecting from cache db: %s' % e, 4)
            return None
        
        if matchedrow:
            addon.log('Found addon id in cache table: %s' % dict(matchedrow), 0)
            return dict(matchedrow)
        else:
            addon.log('No match in local DB for addon_id: %s' % addon_id, 0)
            return False


    def insert_meta_installed(self, addon_id, last_update, movie_covers='false', tv_covers='false', tv_banners='false', movie_backdrops='false', tv_backdrops='false'):
        '''
        Insert a record into addons table

        Insert a unique addon id AFTER a meta data pack has been installed
        
        Args:
            addon_id (str): unique name/id to identify an addon
            last_update (str): date of last meta pack installed - use to check to install meta updates
        Kwargs:
            movie_covers (str): true/false if movie covers has been downloaded/installed
            tv_covers (str): true/false if tv covers has been downloaded/installed            
            movie_backdrops (str): true/false if movie backdrops has been downloaded/installed
            tv_backdrops (str): true/false if tv backdrops has been downloaded/installed
        '''

        if addon_id:
            sql_insert = "INSERT INTO addons(addon_id, movie_covers, tv_covers, tv_banners, movie_backdrops, tv_backdrops, last_update) VALUES (%s,%s,%s,%s,%s,%s,%s)"
        else:
            addon.log('Invalid addon id', 3)
            return
        
        addon.log('Inserting into addons table addon id: %s' % addon_id, 2)
        addon.log('SQL Insert: %s' % sql_insert, 0)
        try:
            self.dbcur.execute(sql_insert, (addon_id, movie_covers, tv_covers, tv_banners, movie_backdrops, tv_backdrops, last_update))
            self.dbcon.commit()            
        except Exception, e:
            addon.log('************* Error inserting into cache db: %s' % e, 4)
            return


    def update_meta_installed(self, addon_id, movie_covers=False, tv_covers=False, tv_banners=False, movie_backdrops=False, tv_backdrops=False, last_update=False):
        '''
        Update a record into addons table

        Insert a unique addon id AFTER a meta data pack has been installed
        
        Args:
            addon_id (str): unique name/id to identify an addon
        Kwargs:
            movie_covers (str): true/false if movie covers has been downloaded/installed
            tv_covers (str): true/false if tv covers has been downloaded/installed
            tv_bannerss (str): true/false if tv banners has been downloaded/installed
            movie_backdrops (str): true/false if movie backdrops has been downloaded/installed
            tv_backdrops (str): true/false if tv backdrops has been downloaded/installed            
        '''

        if addon_id:
            if movie_covers:
                sql_update = "UPDATE addons SET movie_covers = '%s'" % movie_covers
            elif tv_covers:
                sql_update = "UPDATE addons SET tv_covers = '%s'" % tv_covers
            elif tv_banners:
                sql_update = "UPDATE addons SET tv_banners = '%s'" % tv_banners
            elif movie_backdrops:
                sql_update = "UPDATE addons SET movie_backdrops = '%s'" % movie_backdrops
            elif tv_backdrops:
                sql_update = "UPDATE addons SET tv_backdrops = '%s'" % tv_backdrops
            elif last_update:
                sql_update = "UPDATE addons SET last_update = '%s'" % last_update
            else:
                addon.log('No update field specified', 3)
                return
        else:
            addon.log('Invalid addon id', 3)
            return
        
        addon.log('Updating addons table addon id: %s movie_covers: %s tv_covers: %s tv_banners: %s movie_backdrops: %s tv_backdrops: %s last_update: %s' % (addon_id, movie_covers, tv_covers, tv_banners, movie_backdrops, tv_backdrops, last_update), 2)
        addon.log('SQL Update: %s' % sql_update, 0)
        try:    
            self.dbcur.execute(sql_update)
            self.dbcon.commit()
        except Exception, e:
            addon.log('************* Error updating cache db: %s' % e, 4)
            return
                    

    def get_meta(self, media_type, name, imdb_id='', tmdb_id='', year='', overlay=6):
        '''
        Main method to get meta data for movie or tvshow. Will lookup by name/year 
        if no IMDB ID supplied.       
        
        Args:
            media_type (str): 'movie' or 'tvshow'
            name (str): full name of movie/tvshow you are searching            
        Kwargs:
            imdb_id (str): IMDB ID        
            tmdb_id (str): TMDB ID
            year (str): 4 digit year of video, recommended to include the year whenever possible
                        to maximize correct search results.
            overlay (int): To set the default watched status (6=unwatched, 7=watched) on new videos
                        
        Returns:
            DICT of meta data or None if cannot be found.
        '''
       
        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Attempting to retreive meta data for %s: %s %s %s %s' % (media_type, name.decode('utf-8'), year, imdb_id, tmdb_id), 2)
 
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)

        if imdb_id:
            meta = self._cache_lookup_by_id(media_type, imdb_id=imdb_id)
        elif tmdb_id:
            meta = self._cache_lookup_by_id(media_type, tmdb_id=tmdb_id)
        else:
            meta = self._cache_lookup_by_name(media_type, name, year)

        if not meta:
            
            if media_type==self.type_movie:
                meta = self._get_tmdb_meta(imdb_id, tmdb_id, name, year)
            elif media_type==self.type_tvshow:
                meta = self._get_tvdb_meta(imdb_id, name, year)
            
            self._cache_save_video_meta(meta, name, media_type, overlay)

        #We want to send back the name that was passed in   
        meta['title'] = name.decode('utf-8')
              
        #Change cast back into a tuple
        if meta['cast']:
            meta['cast'] = eval(meta['cast'])
            
        #Return a trailer link that will play via youtube addon
        try:
            trailer_id = re.match('^[^v]+v=(.{3,11}).*', meta['trailer_url']).group(1)
            meta['trailer'] = 'plugin://plugin.video.youtube/?action=play_video&videoid=%s' % trailer_id
        except:
            meta['trailer'] = ''
        
        #Ensure we are not sending back any None values, XBMC doesn't like them
        meta = self._remove_none_values(meta)
        
        #Add TVShowTitle infolabel
        if media_type==self.type_tvshow:
            meta['TVShowTitle'] = meta['title']
        
        #Set Watched flag
        meta['playcount'] = self.__set_playcount(meta['overlay'])
        
        #if cache row says there are pre-packed images then either use them or create them
        if meta['imgs_prepacked'] == 'true':

                #define the image paths               
                if media_type == self.type_movie:
                    root_covers = self.mvcovers
                    root_backdrops = self.mvbackdrops
                elif media_type == self.type_tvshow:
                    root_covers = self.tvcovers
                    root_backdrops = self.tvbackdrops
                    root_banners = self.tvbanners
                
                if meta['cover_url']:
                    cover_name = self._picname(meta['cover_url'])
                    cover_path = os.path.join(root_covers, cover_name[0].lower())
                    if self.classmode == 'true':
                        self._downloadimages(meta['cover_url'], cover_path, cover_name)
                    meta['cover_url'] = os.path.join(cover_path, cover_name)
                
                if meta['backdrop_url']:
                    backdrop_name = self._picname(meta['backdrop_url'])
                    backdrop_path=os.path.join(root_backdrops, backdrop_name[0].lower())
                    if self.classmode == 'true':
                        self._downloadimages(meta['backdrop_url'], backdrop_path, backdrop_name)
                    meta['backdrop_url'] = os.path.join(backdrop_path, backdrop_name)

                if meta.has_key('banner_url'):
                    if meta['banner_url']:
                        banner_name = self._picname(meta['banner_url'])
                        banner_path=os.path.join(root_banners, banner_name[0].lower())
                        if self.classmode == 'true':
                            self._downloadimages(meta['banner_url'], banner_path, banner_name)
                        meta['banner_url'] = os.path.join(banner_path, banner_name)        

        addon.log('Returned Meta: %s' % meta, 0)
        return meta  


    def update_meta(self, media_type, name, imdb_id, tmdb_id='', new_imdb_id='', new_tmdb_id='', year=''):
        '''
        Updates and returns meta data for given movie/tvshow, mainly to be used with refreshing individual movies.
        
        Searches local cache DB for record, delete if found, calls get_meta() to grab new data

        name, imdb_id, tmdb_id should be what is currently in the DB in order to find current record
        
        new_imdb_id, new_tmdb_id should be what you would like to update the existing DB record to, which you should have already found
        
        Args:
            name (int): full name of movie you are searching            
            imdb_id (str): IMDB ID of CURRENT entry
        Kwargs:
            tmdb_id (str): TMDB ID of CURRENT entry
            new_imdb_id (str): NEW IMDB_ID to search with
            new_tmdb_id (str): NEW TMDB ID to search with
            year (str): 4 digit year of video, recommended to include the year whenever possible
                        to maximize correct search results.
                        
        Returns:
            DICT of meta data or None if cannot be found.
        '''
        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Updating meta data: %s Old: %s %s New: %s %s Year: %s' % (name.encode('ascii','replace'), imdb_id, tmdb_id, new_imdb_id, new_tmdb_id, year), 2)
        
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)        
        
        if imdb_id:
            meta = self._cache_lookup_by_id(media_type, imdb_id=imdb_id)
        elif tmdb_id:
            meta = self._cache_lookup_by_id(media_type, tmdb_id=tmdb_id)
        else:
            meta = self._cache_lookup_by_name(media_type, name, year)
        
        if meta:
            overlay = meta['overlay']
            self._cache_delete_video_meta(media_type, imdb_id, tmdb_id, name, year)
        else:
            overlay = 6
            addon.log('No match found in cache db', 3)
        
        if not new_imdb_id:
            new_imdb_id = imdb_id
        elif not new_tmdb_id:
            new_tmdb_id = tmdb_id
            
        return self.get_meta(media_type, name, new_imdb_id, new_tmdb_id, year, overlay)


    def _cache_lookup_by_id(self, media_type, imdb_id='', tmdb_id=''):
        '''
        Lookup in SQL DB for video meta data by IMDB ID
        
        Args:
            imdb_id (str): IMDB ID
            media_type (str): 'movie' or 'tvshow'
        Kwargs:
            imdb_id (str): IDMB ID
            tmdb_id (str): TMDB ID                        
        Returns:
            DICT of matched meta data or None if no match.
        '''        
        if media_type == self.type_movie:
            sql_select = "SELECT * FROM movie_meta"
            if imdb_id:
                sql_select = sql_select + " WHERE imdb_id = '%s'" % imdb_id
            else:
                sql_select = sql_select + " WHERE tmdb_id = '%s'" % tmdb_id
        elif media_type == self.type_tvshow:
            sql_select = "SELECT a.*, CASE WHEN b.episode ISNULL THEN 0 ELSE b.episode END AS episode, CASE WHEN c.playcount ISNULL THEN 0 ELSE c.playcount END as playcount FROM tvshow_meta a LEFT JOIN (SELECT imdb_id, count(imdb_id) AS episode FROM episode_meta WHERE imdb_id = '%s' GROUP BY imdb_id) b ON a.imdb_id = b.imdb_id LEFT JOIN (SELECT imdb_id, count(imdb_id) AS playcount FROM episode_meta WHERE imdb_id = '%s' AND overlay=7 GROUP BY imdb_id) c ON a.imdb_id = c.imdb_id WHERE a.imdb_id = '%s'" % (imdb_id, imdb_id, imdb_id)
            if DB == 'mysql':
                sql_select = sql_select.replace("ISNULL", "IS NULL")
        addon.log('Looking up in local cache by id for: %s %s %s' % (media_type, imdb_id, tmdb_id), 0)
        addon.log( 'SQL Select: %s' % sql_select, 0)
        try:    
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()            
        except Exception, e:
            addon.log('************* Error selecting from cache db: %s' % e, 4)
            return None
        
        if matchedrow:
            addon.log('Found meta information by id in cache table: %s' % dict(matchedrow), 0)
            return dict(matchedrow)
        else:
            addon.log('No match in local DB', 0)
            return None


    def _cache_lookup_by_name(self, media_type, name, year=''):
        '''
        Lookup in SQL DB for video meta data by name and year
        
        Args:
            media_type (str): 'movie' or 'tvshow'
            name (str): full name of movie/tvshow you are searching
        Kwargs:
            year (str): 4 digit year of video, recommended to include the year whenever possible
                        to maximize correct search results.
                        
        Returns:
            DICT of matched meta data or None if no match.
        '''        

        name =  self._clean_string(name.lower())
        if media_type == self.type_movie:
            sql_select = "SELECT * FROM movie_meta WHERE title = '%s'" % name
        elif media_type == self.type_tvshow:
            sql_select = "SELECT a.*, CASE WHEN b.episode ISNULL THEN 0 ELSE b.episode END AS episode, CASE WHEN c.playcount ISNULL THEN 0 ELSE c.playcount END as playcount FROM tvshow_meta a LEFT JOIN (SELECT imdb_id, count(imdb_id) AS episode FROM episode_meta GROUP BY imdb_id) b ON a.imdb_id = b.imdb_id LEFT JOIN (SELECT imdb_id, count(imdb_id) AS playcount FROM episode_meta WHERE overlay=7 GROUP BY imdb_id) c ON a.imdb_id = c.imdb_id WHERE a.title = '%s'" % name
            if DB == 'mysql':
                sql_select = sql_select.replace("ISNULL", "IS NULL")
        addon.log('Looking up in local cache by name for: %s %s %s' % (media_type, name, year), 0)
        
        # movie_meta doesn't have a year column
        if year and media_type == self.type_movie:
            sql_select = sql_select + " AND year = %s" % year
        addon.log('SQL Select: %s' % sql_select, 0)
        
        try:
            self.dbcur.execute(sql_select)            
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error selecting from cache db: %s' % e, 4)
            pass
            
        if matchedrow:
            addon.log('Found meta information by name in cache table: %s' % dict(matchedrow), 0)
            return dict(matchedrow)
        else:
            addon.log('No match in local DB', 0)
            return None


    def _cache_lookup_by_name(self, media_type, name, year=''):
        '''
        Lookup in SQL DB for video meta data by name and year
        
        Args:
            media_type (str): 'movie' or 'tvshow'
            name (str): full name of movie/tvshow you are searching
        Kwargs:
            year (str): 4 digit year of video, recommended to include the year whenever possible
                        to maximize correct search results.
                        
        Returns:
            DICT of matched meta data or None if no match.
        '''        

        name =  self._clean_string(name.lower())
        if media_type == self.type_movie:
            sql_select = "SELECT * FROM movie_meta WHERE title = '%s'" % name
        elif media_type == self.type_tvshow:
            sql_select = "SELECT a.*, CASE WHEN b.episode ISNULL THEN 0 ELSE b.episode END AS episode, CASE WHEN c.playcount ISNULL THEN 0 ELSE c.playcount END as playcount FROM tvshow_meta a LEFT JOIN (SELECT imdb_id, count(imdb_id) AS episode FROM episode_meta GROUP BY imdb_id) b ON a.imdb_id = b.imdb_id LEFT JOIN (SELECT imdb_id, count(imdb_id) AS playcount FROM episode_meta WHERE overlay=7 GROUP BY imdb_id) c ON a.imdb_id = c.imdb_id WHERE a.title = '%s'" % name
            if DB == 'mysql':
                sql_select = sql_select.replace("ISNULL", "IS NULL")
        addon.log('Looking up in local cache by name for: %s %s %s' % (media_type, name, year), 0)
        
        # movie_meta doesn't have a year column
        # if year and media_type == self.type_movie:
            # sql_select = sql_select + " AND year = %s" % year
        addon.log('SQL Select: %s' % sql_select, 0)
        
        try:
            self.dbcur.execute(sql_select)            
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error selecting from cache db: %s' % e, 4)
            pass
            
        if matchedrow:
            addon.log('Found meta information by name in cache table: %s' % dict(matchedrow), 0)
            return dict(matchedrow)
        else:
            addon.log('No match in local DB', 0)
            return None

    
    def _cache_save_video_meta(self, meta, name, media_type, overlay=6):
        '''
        Saves meta data to SQL table given type
        
        Args:
            meta (dict): meta data of video to be added to database
            media_type (str): 'movie' or 'tvshow'
        Kwargs:
            overlay (int): To set the default watched status (6=unwatched, 7=watched) on new videos                        
        '''            
        if media_type == self.type_movie:
            table='movie_meta'
        elif media_type == self.type_tvshow:
            table='tvshow_meta'
        
        #strip title
        meta['title'] =  self._clean_string(name.lower())
               
        #Select on either IMDB ID or name + premiered
        if meta['imdb_id']:
            sql_select = "SELECT * FROM %s WHERE imdb_id = '%s'" % (table, meta['imdb_id'])
        else:           
            sql_select = "SELECT * FROM %s WHERE title = '%s'" % (table, meta['title'])

            if meta.has_key('year') and media_type == self.type_movie:
                if meta['year']:
                    sql_select = sql_select + " AND year = '%s'" % meta['year']

        addon.log('Checking if entry already exists in cache table: %s' % table, 0)
        addon.log('SQL SELECT: %s' % sql_select, 0)
        
        try:          
            self.dbcur.execute(sql_select) #select database row
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from table: %s with error: %s' % (table, e), 4)
            pass
            
        if matchedrow:
            addon.log('Matched Row found, deleting table entry', 0)
            sql_delete = "DELETE FROM %s WHERE imdb_id = '%s'" % (table, meta['imdb_id'])
            addon.log('SQL DELETE: %s' % sql_delete, 0)
            
            try:
                self.dbcur.execute(sql_delete)
            except Exception, e:
                addon.log('************* Error attempting to delete from %s cache table: %s ' % (table, e), 4)
                addon.log('Meta data: %s' % meta, 4)
                pass
        
        if meta.has_key('cast'):
            meta['cast'] = str(meta['cast'])

        #set default overlay - watched status
        meta['overlay'] = overlay
        
        addon.log('Saving cache information: %s' % meta, 0)

        try:
            if media_type == self.type_movie:
                sql_insert = self.__insert_from_dict(table, 22)
                addon.log('SQL INSERT: %s' % sql_insert, 0)

                self.dbcur.execute(sql_insert, (meta['imdb_id'], meta['tmdb_id'], meta['title'],
                                meta['year'], meta['director'], meta['writer'], meta['tagline'], meta['cast'],
                                meta['rating'], meta['votes'], meta['duration'], meta['plot'], meta['mpaa'],
                                meta['premiered'], meta['genre'], meta['studio'], meta['thumb_url'], meta['cover_url'],
                                meta['trailer_url'], meta['backdrop_url'], meta['imgs_prepacked'], meta['overlay']))
            elif media_type == self.type_tvshow:
                sql_insert = self.__insert_from_dict(table, 19)
                addon.log('SQL INSERT: %s' % sql_insert, 0)

                self.dbcur.execute(sql_insert, (meta['imdb_id'], meta['tvdb_id'], meta['title'], meta['year'], 
                        meta['cast'], meta['rating'], meta['duration'], meta['plot'], meta['mpaa'],
                        meta['premiered'], meta['genre'], meta['studio'], meta['status'], meta['banner_url'],
                        meta['cover_url'], meta['trailer_url'], meta['backdrop_url'], meta['imgs_prepacked'], meta['overlay']))
            self.dbcon.commit()
        except Exception, e:
            addon.log('************* Error attempting to insert into %s cache table: %s ' % (table, e), 4)
            addon.log('Meta data: %s' % meta, 4)
            pass 


    def _cache_delete_video_meta(self, media_type, imdb_id, tmdb_id, name, year):
        '''
        Delete meta data from SQL table
        
        Args:
            media_type (str): 'movie' or 'tvshow'
            imdb_id (str): IMDB ID
            tmdb_id (str): TMDB ID   
            name (str): Full movie name
            year (int): Movie year
                        
        '''         
        
        if media_type == self.type_movie:
            table = 'movie_meta'
        elif media_type == self.type_tvshow:
            table = 'tvshow_meta'
            
        if imdb_id:
            sql_delete = "DELETE FROM %s WHERE imdb_id = '%s'" % (table, imdb_id)
        elif tmdb_id:
            sql_delete = "DELETE FROM %s WHERE tmdb_id = '%s'" % (table, tmdb_id)
        else:
            name =  self._clean_string(name.lower())
            sql_delete = "DELETE FROM %s WHERE title = '%s'" % (table, name)
            if year:
                sql_delete = sql_delete + ' AND year = %s' % (year)

        addon.log('Deleting table entry: %s %s %s %s ' % (imdb_id, tmdb_id, name, year), 0)
        addon.log('SQL DELETE: %s' % sql_delete, 0)
        try:
            self.dbcur.execute(sql_delete)
        except Exception, e:
            addon.log('************* Error attempting to delete from cache table: %s ' % e, 4)
            pass    
        

    def _get_tmdb_meta(self, imdb_id, tmdb_id, name, year=''):
        '''
        Requests meta data from TMDB and creates proper dict to send back
        
        Args:
            imdb_id (str): IMDB ID
            name (str): full name of movie you are searching
        Kwargs:
            year (str): 4 digit year of movie, when imdb_id is not available it is recommended
                        to include the year whenever possible to maximize correct search results.
                        
        Returns:
            DICT. It must also return an empty dict when
            no movie meta info was found from tmdb because we should cache
            these "None found" entries otherwise we hit tmdb alot.
        '''        
        
        tmdb = TMDB()        
        meta = tmdb.tmdb_lookup(name,imdb_id,tmdb_id, year)       
        
        if meta is None:
            # create an empty dict so below will at least populate empty data for the db insert.
            meta = {}

        return self._format_tmdb_meta(meta, imdb_id, name, year)


    def _format_tmdb_meta(self, md, imdb_id, name, year):
        '''
        Copy tmdb to our own for conformity and eliminate KeyError. Set default for values not returned
        
        Args:
            imdb_id (str): IMDB ID
            name (str): full name of movie you are searching
        Kwargs:
            year (str): 4 digit year of movie, when imdb_id is not available it is recommended
                        to include the year whenever possible to maximize correct search results.
                        
        Returns:
            DICT. It must also return an empty dict when
            no movie meta info was found from tvdb because we should cache
            these "None found" entries otherwise we hit tvdb alot.
        '''      
        
        #Intialize movie_meta dictionary    
        meta = self._init_movie_meta(imdb_id, md.get('id', ''), name, year)
        
        meta['imdb_id'] = md.get('imdb_id', imdb_id)
        meta['title'] = md.get('name', name)      
        meta['tagline'] = md.get('tagline', '')
        meta['rating'] = float(md.get('rating', 0))
        meta['votes'] = str(md.get('votes', ''))
        meta['duration'] = str(md.get('runtime', 0))
        meta['plot'] = md.get('overview', '')
        meta['mpaa'] = md.get('certification', '')       
        meta['premiered'] = md.get('released', '')
        meta['director'] = md.get('director', '')
        meta['writer'] = md.get('writer', '')       

        #Do whatever we can to set a year, if we don't have one lets try to strip it from premiered
        if not year and meta['premiered']:
            #meta['year'] = int(self._convert_date(meta['premiered'], '%Y-%m-%d', '%Y'))
            meta['year'] = int(meta['premiered'][:4])
            
        meta['trailer_url'] = md.get('trailer', '')
        meta['genre'] = md.get('genre', '')
        
        #Get cast, director, writers
        cast_list = []
        cast_list = md.get('cast','')
        if cast_list:
            for cast in cast_list:
                job=cast.get('job','')
                if job == 'Actor':
                    meta['cast'].append((cast.get('name',''),cast.get('character','') ))
                elif job == 'Director':
                    meta['director'] = cast.get('name','')
                elif job == 'Screenplay':
                    if meta['writer']:
                        meta['writer'] = meta['writer'] + ' / ' + cast.get('name','')
                    else:
                        meta['writer'] = cast.get('name','')
                    
        genre_list = []
        genre_list = md.get('genres', '')
        for genre in genre_list:
            if meta['genre'] == '':
                meta['genre'] = genre.get('name','')
            else:
                meta['genre'] = meta['genre'] + ' / ' + genre.get('name','')
        
        if md.has_key('tvdb_studios'):
            meta['studio'] = md.get('tvdb_studios', '')
        try:
            meta['studio'] = (md.get('studios', '')[0])['name']
        except:
            try:
                meta['studio'] = (md.get('studios', '')[1])['name']
            except:
                try:
                    meta['studio'] = (md.get('studios', '')[2])['name']
                except:
                    try:    
                        meta['studio'] = (md.get('studios', '')[3])['name']
                    except:
                        addon.log('Studios failed: %s ' % md.get('studios', ''), 0)
                        pass
        
        meta['cover_url'] = md.get('cover_url', '')
        if md.has_key('posters'):
            # find first thumb poster url
            for poster in md['posters']:
                if poster['image']['size'] == 'thumb':
                    meta['thumb_url'] = poster['image']['url']
                    break
            # find first cover poster url
            for poster in md['posters']:
                if poster['image']['size'] == 'cover':
                    meta['cover_url'] = poster['image']['url']
                    break

        if md.has_key('backdrops'):
            # find first original backdrop url
            for backdrop in md['backdrops']:
                if backdrop['image']['size'] == 'original':
                    meta['backdrop_url'] = backdrop['image']['url']
                    break

        return meta
        
        
    def _get_tvdb_meta(self, imdb_id, name, year=''):
        '''
        Requests meta data from TVDB and creates proper dict to send back
        
        Args:
            imdb_id (str): IMDB ID
            name (str): full name of movie you are searching
        Kwargs:
            year (str): 4 digit year of movie, when imdb_id is not available it is recommended
                        to include the year whenever possible to maximize correct search results.
                        
        Returns:
            DICT. It must also return an empty dict when
            no movie meta info was found from tvdb because we should cache
            these "None found" entries otherwise we hit tvdb alot.
        '''      
        addon.log('Starting TVDB Lookup', 0)
        tvdb = TheTVDB()
        tvdb_id = ''
        
        try:
            if imdb_id:
                tvdb_id = tvdb.get_show_by_imdb(imdb_id)
        except Exception, e:
            addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
            tvdb_id = ''
            pass
            
        #Intialize tvshow meta dictionary
        meta = self._init_tvshow_meta(imdb_id, tvdb_id, name, year)

        # if not found by imdb, try by name
        if tvdb_id == '':
            try:
                #If year is passed in, add it to the name for better TVDB search results
                if year:
                    name = name + ' ' + year
                show_list=tvdb.get_matching_shows(name)
            except Exception, e:
                addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
                show_list = []
                pass
            addon.log('Found TV Show List: %s' % show_list, 0)
            tvdb_id=''
            prob_id=''
            for show in show_list:
                (junk1, junk2, junk3) = show
                #if we match imdb_id or full name (with year) then we know for sure it is the right show
                if junk3==imdb_id or self._string_compare(self._clean_string(junk2),self._clean_string(name)):
                    tvdb_id=self._clean_string(junk1)
                    if not imdb_id:
                        imdb_id=self._clean_string(junk3)
                    break
                #if we match just the cleaned name (without year) keep the tvdb_id
                elif self._string_compare(self._clean_string(junk2),self._clean_string(name)):
                    prob_id = junk1
                    if not imdb_id:
                        imdb_id = self_clean_string(junk3)
            if tvdb_id == '' and prob_id != '':
                tvdb_id = self._clean_string(prob_id)

        if tvdb_id:
            addon.log('Show *** ' + name + ' *** found in TVdb. Getting details...', 0)

            try:
                show = tvdb.get_show(tvdb_id)
            except Exception, e:
                addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
                show = None
                pass
            
            if show is not None:
                meta['imdb_id'] = imdb_id
                meta['tvdb_id'] = tvdb_id
                meta['title'] = name
                if str(show.rating) != '' and show.rating != None:
                    meta['rating'] = float(show.rating)
                meta['duration'] = show.runtime
                meta['plot'] = show.overview
                meta['mpaa'] = show.content_rating
                meta['premiered'] = str(show.first_aired)

                #Do whatever we can to set a year, if we don't have one lets try to strip it from show.first_aired/premiered
                if not year and show.first_aired:
                        #meta['year'] = int(self._convert_date(meta['premiered'], '%Y-%m-%d', '%Y'))
                        meta['year'] = int(meta['premiered'][:4])

                if show.genre != '':
                    temp = show.genre.replace("|",",")
                    temp = temp[1:(len(temp)-1)]
                    meta['genre'] = temp
                meta['studio'] = show.network
                meta['status'] = show.status
                if show.actors:
                    for actor in show.actors:
                        meta['cast'].append(actor)
                meta['banner_url'] = show.banner_url
                meta['imgs_prepacked'] = self.classmode
                meta['cover_url'] = show.poster_url
                meta['backdrop_url'] = show.fanart_url
                meta['overlay'] = 6

                if meta['plot'] == 'None' or meta['plot'] == '' or meta['plot'] == 'TBD' or meta['plot'] == 'No overview found.' or meta['rating'] == 0 or meta['duration'] == 0 or meta['cover_url'] == '':
                    addon.log(' Some info missing in TVdb for TVshow *** '+ name + ' ***. Will search imdb for more', 0)
                    tmdb = TMDB()
                    imdb_meta = tmdb.search_imdb(name, imdb_id)
                    if imdb_meta:
                        imdb_meta = tmdb.update_imdb_meta(meta, imdb_meta)
                        if imdb_meta.has_key('overview'):
                            meta['plot'] = imdb_meta['overview']
                        if imdb_meta.has_key('rating'):
                            meta['rating'] = float(imdb_meta['rating'])
                        if imdb_meta.has_key('runtime'):
                            meta['duration'] = imdb_meta['runtime']
                        if imdb_meta.has_key('cast'):
                            meta['cast'] = imdb_meta['cast']
                        if imdb_meta.has_key('cover_url'):
                            meta['cover_url'] = imdb_meta['cover_url']

                return meta
            else:
                tmdb = TMDB()
                imdb_meta = tmdb.search_imdb(name, imdb_id)
                if imdb_meta:
                    meta = tmdb.update_imdb_meta(meta, imdb_meta)
                return meta    
        else:
            return meta


    def search_movies(self, name):
        '''
        Requests meta data from TMDB for any movie matching name
        
        Args:
            name (str): full name of movie you are searching
                        
        Returns:
            Arry of dictionaries with trimmed down meta data, only returned data that is required:
            - IMDB ID
            - TMDB ID
            - Name
            - Year
        ''' 
        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Meta data refresh - searching for movie: %s' % name, 2)
        tmdb = TMDB()
        movie_list = []
        meta = tmdb.tmdb_search(name)
        if meta:
            for movie in meta:
                if movie['released']:
                    #year = self._convert_date(movie['released'], '%Y-%m-%d', '%Y')
                    year = movie['released'][:4]
                else:
                    year = None
                movie_list.append({'title': movie['name'], 'imdb_id': movie['imdb_id'], 'tmdb_id': movie['id'], 'year': year})
        else:
            addon.log('No results found', 2)
            return None

        addon.log('Returning results: %s' % movie_list, 0)
        return movie_list

            
    def get_episode_meta(self, tvshowtitle, imdb_id, season, episode, air_date='', episode_title='', overlay=''):
        '''
        Requests meta data from TVDB for TV episodes, searches local cache db first.
        
        Args:
            tvshowtitle (str): full name of tvshow you are searching
            imdb_id (str): IMDB ID
            season (int): tv show season number, number only no other characters
            episode (int): tv show episode number, number only no other characters
        Kwargs:
            air_date (str): In cases where episodes have no episode number but only an air date - eg. daily talk shows
            episode_title (str): The title of the episode, gets set to the title infolabel which must exist
            overlay (int): To set the default watched status (6=unwatched, 7=watched) on new videos
                        
        Returns:
            DICT. It must also return an empty dict when
            no meta info was found in order to save these.
        '''  
              
        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Attempting to retreive episode meta data for: imdbid: %s season: %s episode: %s air_date: %s' % (imdb_id, season, episode, air_date), 2)
               
        if not season:
            season = 0
        if not episode:
            episode = 0
        
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)

        #Find tvdb_id for the TVshow
        tvdb_id = self._get_tvdb_id(tvshowtitle, imdb_id)

        #Check if it exists in local cache first
        meta = self._cache_lookup_episode(imdb_id, tvdb_id, season, episode, air_date)
        
        #If not found lets scrape online sources
        if not meta:

            #I need a tvdb id to scrape The TVDB
            if tvdb_id:
                meta = self._get_tvdb_episode_data(tvdb_id, season, episode, air_date)
            else:
                addon.log("No TVDB ID available, could not find TVshow with imdb: %s " % imdb_id, 0)

            #If nothing found
            if not meta:
                #Init episode meta structure
                meta = self.__init_episode_meta(imdb_id, tvdb_id, episode_title, season, episode, air_date)
            
            #set overlay if used, else default to unwatched
            if overlay:
                meta['overlay'] = int(overlay)
            else:
                meta['overlay'] = 6
                    
            if not meta['title']:
                meta['title']= episode_title
            
            meta['tvdb_id'] = tvdb_id
            meta['imdb_id'] = imdb_id
            meta['cover_url'] = meta['poster']
            meta = self._get_tv_extra(meta)
                           
            self._cache_save_episode_meta(meta)

        #Ensure we are not sending back any None values, XBMC doesn't like them
        meta = self._remove_none_values(meta)

        #Set Watched flag
        meta['playcount'] = self.__set_playcount(meta['overlay'])
        
        #Add key for subtitles to work
        meta['TVShowTitle']= tvshowtitle
        
        addon.log('Returned Meta: %s' % meta, 0)
        return meta


    def _get_tv_extra(self, meta):
        '''
        When requesting episode information, not all data may be returned
        Fill in extra missing meta information from tvshow_meta table which should
        have already been populated.
        
        Args:
            meta (dict): current meta dict
                        
        Returns:
            DICT containing the extra values
        '''
        
        if meta['imdb_id']:
            sql_select = "SELECT * FROM tvshow_meta WHERE imdb_id = '%s'" % meta['imdb_id']
        elif meta['tvdb_id']:
            sql_select = "SELECT * FROM tvshow_meta WHERE tvdb_id = '%s'" % meta['tvdb_id']
        else:
            sql_select = "SELECT * FROM tvshow_meta WHERE title = '%s'" % self._clean_string(meta['title'].lower())
            
        addon.log('Retrieving extra TV Show information from tvshow_meta', 0)
        addon.log('SQL SELECT: %s' % sql_select, 0)
        
        try:     
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from tvshow_meta table: %s ' % e, 4)
            pass   

        if matchedrow:
            match = dict(matchedrow)
            meta['genre'] = match['genre']
            meta['duration'] = match['duration']
            meta['studio'] = match['studio']
            meta['mpaa'] = match['mpaa']
            meta['backdrop_url'] = match['backdrop_url']
        else:
            meta['genre'] = ''
            meta['duration'] = '0'
            meta['studio'] = ''
            meta['mpaa'] = ''
            meta['backdrop_url'] = ''

        return meta


    def _get_tvdb_id(self, name, imdb_id):
        '''
        Retrieves TVID for a tv show that has already been scraped and saved in cache db.
        
        Used when scraping for season and episode data
        
        Args:
            name (str): full name of tvshow you are searching            
            imdb_id (str): IMDB ID
                        
        Returns:
            (str) imdb_id 
        '''      
        
        #clean tvshow name of any extras       
        name =  self._clean_string(name.lower())
        
        if imdb_id:
            sql_select = "SELECT tvdb_id FROM tvshow_meta WHERE imdb_id = '%s'" % imdb_id
        elif name:
            sql_select = "SELECT tvdb_id FROM tvshow_meta WHERE title = '%s'" % name
            
        addon.log('Retrieving TVDB ID', 0)
        addon.log('SQL SELECT: %s' % sql_select, 0)
        
        try:
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from tvshow_meta table: %s ' % e, 4)
            pass
                        
        if matchedrow:
                return dict(matchedrow)['tvdb_id']
        else:
            return None


    def update_episode_meta(self, name, imdb_id, season, episode, tvdb_id='', new_imdb_id='', new_tvdb_id=''):
        '''
        Updates and returns meta data for given episode, 
        mainly to be used with refreshing individual tv show episodes.
        
        Searches local cache DB for record, delete if found, calls get_episode_meta() to grab new data
               
        
        Args:
            name (int): full name of movie you are searching
            imdb_id (str): IMDB ID
            season (int): season number
            episode (int): episode number
        Kwargs:
            tvdb_id (str): TVDB ID
                        
        Returns:
            DICT of meta data or None if cannot be found.
        '''
        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Updating episode meta data: %s IMDB: %s SEASON: %s EPISODE: %s TVDB ID: %s NEW IMDB ID: %s NEW TVDB ID: %s' % (name, imdb_id, season, episode, tvdb_id, new_imdb_id, new_tvdb_id), 2)

      
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)
        else:
            imdb_id = ''

        #Find tvdb_id for the TVshow
        tvdb_id = self._get_tvdb_id(name, imdb_id)
        
        #Lookup in cache table for existing entry
        meta = self._cache_lookup_episode(imdb_id, tvdb_id, season, episode)
        
        #We found an entry in the DB, so lets delete it
        if meta:
            overlay = meta['overlay']
            self._cache_delete_episode_meta(imdb_id, tvdb_id, name, season, episode)
        else:
            overlay = 6
            addon.log('No match found in cache db', 0)
       
        if not new_imdb_id:
            new_imdb_id = imdb_id
        elif not new_tvdb_id:
            new_tvdb_id = tvdb_id
            
        return self.get_episode_meta(name, imdb_id, season, episode, overlay)


    def _cache_lookup_episode(self, imdb_id, tvdb_id, season, episode, air_date=''):
        '''
        Lookup in local cache db for episode data
        
        Args:
            imdb_id (str): IMDB ID
            tvdb_id (str): TheTVDB ID
            season (str): tv show season number, number only no other characters
            episode (str): tv show episode number, number only no other characters
        Kwargs:
            air_date (str): date episode was aired - YYYY-MM-DD

        Returns:
            DICT. Returns results found or None.
        ''' 
        addon.log('Looking up episode data in cache db, imdb id: %s season: %s episode: %s air_date: %s' % (imdb_id, season, episode, air_date), 0)
        
        try:

            sql_select = ('SELECT '
                               'episode_meta.title as title, '
                               'episode_meta.plot as plot, '
                               'episode_meta.director as director, '
                               'episode_meta.writer as writer, '
                               'tvshow_meta.genre as genre, '
                               'tvshow_meta.duration as duration, '
                               'episode_meta.premiered as premiered, '
                               'tvshow_meta.studio as studio, '
                               'tvshow_meta.mpaa as mpaa, '
                               'tvshow_meta.title as TVShowTitle, '
                               'episode_meta.imdb_id as imdb_id, '
                               'episode_meta.rating as rating, '
                               '"" as trailer_url, '
                               'episode_meta.season as season, '
                               'episode_meta.episode as episode, '
                               'episode_meta.overlay as overlay, '
                               'tvshow_meta.backdrop_url as backdrop_url, '                               
                               'episode_meta.poster as cover_url ' 
                               'FROM episode_meta, tvshow_meta '
                               'WHERE episode_meta.imdb_id = tvshow_meta.imdb_id AND '
                               'episode_meta.tvdb_id = tvshow_meta.tvdb_id AND '
                               'episode_meta.imdb_id = "%s" AND episode_meta.tvdb_id = "%s" AND '
                               )  % (imdb_id, tvdb_id)
            
            #If air_date is supplied, select on it instead of season & episode #
            if air_date:
                sql_select = sql_select + 'episode_meta.premiered = "%s" ' % air_date
            else:
                sql_select = sql_select + 'season = %s AND episode_meta.episode = %s ' % (season, episode)

            addon.log('SQL SELECT: %s' % sql_select, 0)
            
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from Episode table: %s ' % e, 4)
            return None
                        
        if matchedrow:
            addon.log('Found episode meta information in cache table: %s' % dict(matchedrow), 0)
            return dict(matchedrow)
        else:
            return None


    def _cache_delete_episode_meta(self, imdb_id, tvdb_id, name, season, episode, air_date=''):
        '''
        Delete meta data from SQL table
        
        Args:
            imdb_id (str): IMDB ID
            tvdb_id (str): TVDB ID
            name (str): Episode title
            season (int): Season #
            episode(int): Episode #
        Kwargs:
            air_date (str): Air Date of episode
        '''

        if imdb_id:
            sql_delete = "DELETE FROM episode_meta WHERE imdb_id = '%s' AND tvdb_id = '%s' AND season = %s" % (imdb_id, tvdb_id, season)
            if air_date:
                sql_delete = sql_delete + ' AND premiered = "%s"' % air_date
            else:
                sql_delete = sql_delete + ' AND episode = %s' % episode

        addon.log('Deleting table entry: IMDB: %s TVDB: %s Title: %s Season: %s Episode: %s ' % (imdb_id, tvdb_id, name, season, episode), 0)
        addon.log('SQL DELETE: %s' % sql_delete, 0)
        try:
            self.dbcur.execute(sql_delete)
        except Exception, e:
            addon.log('************* Error attempting to delete from episode cache table: %s ' % e, 4)
            pass


    def _get_tvdb_episode_data(self, tvdb_id, season, episode, air_date=''):
        '''
        Initiates lookup for episode data on TVDB
        
        Args:
            tvdb_id (str): TVDB id
            season (str): tv show season number, number only no other characters
            episode (str): tv show episode number, number only no other characters
        Kwargs:
            air_date (str): Date episode was aired
                        
        Returns:
            DICT. Data found from lookup
        '''      
        
        meta = {}
        tvdb = TheTVDB()
        if air_date:
            try:
                episode = tvdb.get_episode_by_airdate(tvdb_id, air_date)
            except:
                addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
                episode = None
                pass
                
            
            #We do this because the airdate method returns just a part of the overview unfortunately
            if episode:
                ep_id = episode.id
                if ep_id:
                    try:
                        episode = tvdb.get_episode(ep_id)
                    except:
                        addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
                        episode = None
                        pass
        else:
            try:
                episode = tvdb.get_episode_by_season_ep(tvdb_id, season, episode)
            except:
                addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
                episode = None
                pass
            
        if episode is None:
            return None
        
        meta['episode_id'] = episode.id
        meta['plot'] = self._check(episode.overview)
        if episode.guest_stars:
            guest_stars = episode.guest_stars
            if guest_stars.startswith('|'):
                guest_stars = guest_stars[1:-1]
            guest_stars = guest_stars.replace('|', ', ')
            meta['plot'] = meta['plot'] + '\n\nGuest Starring: ' + guest_stars
        meta['rating'] = float(self._check(episode.rating,0))
        meta['premiered'] = self._check(episode.first_aired)
        meta['title'] = self._check(episode.name)
        meta['poster'] = self._check(episode.image)
        meta['director'] = self._check(episode.director)
        meta['writer'] = self._check(episode.writer)
        meta['season'] = int(self._check(episode.season_number,0))
        meta['episode'] = int(self._check(episode.episode_number,0))
              
        return meta


    def _check(self, value, ret=None):
        if value is None or value == '':
            if ret == None:
                return ''
            else:
                return ret
        else:
            return value
            
        
    def _cache_save_episode_meta(self, meta):
        '''
        Save episode data to local cache db.
        
        Args:
            meta (dict): episode data to be stored
                        
        '''      
        if meta['imdb_id']:
            sql_select = 'SELECT * FROM episode_meta WHERE imdb_id = "%s" AND season = %s AND episode = %s AND premiered = "%s"'  % (meta['imdb_id'], meta['season'], meta['episode'], meta['premiered'])
            sql_delete = 'DELETE FROM episode_meta WHERE imdb_id = "%s" AND season = %s AND episode = %s AND premiered = "%s"'  % (meta['imdb_id'], meta['season'], meta['episode'], meta['premiered'])
        elif meta['tvdb_id']:
            sql_select = 'SELECT * FROM episode_meta WHERE tvdb_id = "%s" AND season = %s AND episode = %s AND premiered = "%s"'  % (meta['tvdb_id'], meta['season'], meta['episode'], meta['premiered'])
            sql_delete = 'DELETE FROM episode_meta WHERE tvdb_id = "%s" AND season = %s AND episode = %s AND premiered = "%s"'  % (meta['tvdb_id'], meta['season'], meta['episode'], meta['premiered'])
        else:         
            sql_select = 'SELECT * FROM episode_meta WHERE title = "%s" AND season = %s AND episode = %s AND premiered = "%s"'  % (self._clean_string(meta['title'].lower()), meta['season'], meta['episode'], meta['premiered'])
            sql_delete = 'DELETE FROM episode_meta WHERE title = "%s" AND season = %s AND episode = %s AND premiered = "%s"'  % (self._clean_string(meta['title'].lower()), meta['season'], meta['episode'], meta['premiered'])
        addon.log('Saving Episode Meta', 0)
        addon.log('SQL Select: %s' % sql_select, 0)
        
        try: 
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
            if matchedrow:
                    addon.log('Episode matched row found, deleting table entry', 0)
                    addon.log('SQL Delete: %s' % sql_delete, 0)
                    self.dbcur.execute(sql_delete) 
        except Exception, e:
            addon.log('************* Error attempting to delete from cache table: %s ' % e, 4)
            addon.log('Meta data: %' % meta, 4)
            pass        
        
        addon.log('Saving episode cache information: %s' % meta, 0)
        try:
            sql_insert = self.__insert_from_dict('episode_meta', 13)
            addon.log('SQL INSERT: %s' % sql_insert, 0)
            self.dbcur.execute(sql_insert, (meta['imdb_id'], meta['tvdb_id'], meta['episode_id'], meta['season'], 
                                meta['episode'], meta['title'], meta['director'], meta['writer'], meta['plot'], 
                                meta['rating'], meta['premiered'], meta['poster'], meta['overlay'])
            )
            self.dbcon.commit()
        except Exception, e:
            addon.log('************* Error attempting to insert into episodes cache table: %s ' % e, 4)
            addon.log('Meta data: %s' % meta, 4)
            pass        


    def update_trailer(self, media_type, imdb_id, trailer, tmdb_id=''):
        '''
        Change watched status on video
        
        Args:
            media_type (str): media_type of video to update, 'movie', 'tvshow' or 'episode'
            imdb_id (str): IMDB ID
            trailer (str): url of youtube video
        Kwargs:            
            tmdb_id (str): TMDB ID
                        
        '''      
        if media_type == 'movie':
            table='movie_meta'
        elif media_type == 'tvshow':
            table='tvshow_meta'
        
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)

        if imdb_id:
            sql_update = "UPDATE %s set trailer_url='%s' WHERE imdb_id = '%s'" % (table, trailer, imdb_id)
        elif tmdb_id:
            sql_update = "UPDATE %s set trailer_url='%s' WHERE tmdb_id = '%s'" % (table, trailer, tmdb_id)
               
        addon.log('Updating trailer for type: %s, imdb id: %s, tmdb_id: %s, trailer: %s' % (media_type, imdb_id, tmdb_id, trailer), 0)
        addon.log('SQL UPDATE: %s' % sql_update, 0)
        try:    
            self.dbcur.execute(sql_update)
            self.dbcon.commit()
        except Exception, e:
            addon.log('************* Error attempting to update table: %s ' % e, 4)
            pass          


    def change_watched(self, media_type, name, imdb_id, tmdb_id='', season='', episode='', year='', watched=''):
        '''
        Change watched status on video
        
        Args:
            imdb_id (str): IMDB ID
            media_type (str): type of video to update, 'movie', 'tvshow' or 'episode'
            name (str): name of video
        Kwargs:            
            season (int): season number
            episode (int): episode number
            year (int): Year
            watched (int): Can specify what to change watched status (overlay) to
                        
        '''   
        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Updating watched flag for: %s %s %s %s %s %s %s %s %s' % (media_type, name, imdb_id, tmdb_id, season, episode, year, watched, premiered), 2)

        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)

        tvdb_id = ''
        if media_type in (self.type_tvshow, self.type_season):
            tvdb_id = self._get_tvdb_id(name, imdb_id)                                  
        
        if media_type in (self.type_movie, self.type_tvshow, self.type_season):
            if not watched:
                watched = self._get_watched(media_type, imdb_id, tmdb_id, season=season)
                if watched == 6:
                    watched = 7
                else:
                    watched = 6
            self._update_watched(imdb_id, media_type, watched, tmdb_id=tmdb_id, name=self._clean_string(name.lower()), year=year, season=season, tvdb_id=tvdb_id)                
        elif media_type == self.type_episode:
            if tvdb_id is None:
                tvdb_id = ''
            tmp_meta = {}
            tmp_meta['imdb_id'] = imdb_id
            tmp_meta['tvdb_id'] = tvdb_id 
            tmp_meta['title'] = name
            tmp_meta['season']  = season
            tmp_meta['episode'] = episode
            
            if not watched:
                watched = self._get_watched_episode(tmp_meta)
                if watched == 6:
                    watched = 7
                else:
                    watched = 6
            self._update_watched(imdb_id, media_type, watched, name=name, season=season, episode=episode, tvdb_id=tvdb_id)
                

    def _update_watched(self, imdb_id, media_type, new_value, tmdb_id='', name='', year='', season='', episode='', tvdb_id=''):
        '''
        Commits the DB update for the watched status
        
        Args:
            imdb_id (str): IMDB ID
            media_type (str): type of video to update, 'movie', 'tvshow' or 'episode'
            new_value (int): value to update overlay field with
        Kwargs:
            name (str): name of video        
            season (str): season number
            tvdb_id (str): tvdb id of tvshow                        

        '''      
        if media_type == self.type_movie:
            if imdb_id:
                sql_update="UPDATE movie_meta SET overlay = %s WHERE imdb_id = '%s'" % (new_value, imdb_id)
            elif tmdb_id:
                sql_update="UPDATE movie_meta SET overlay = %s WHERE tmdb_id = '%s'" % (new_value, tmdb_id)
            else:
                sql_update="UPDATE movie_meta SET overlay = %s WHERE title = '%s'" % (new_value, name)
                if year:
                    sql_update = sql_update + ' AND year=%s' % year
        elif media_type == self.type_tvshow:
            if imdb_id:
                sql_update="UPDATE tvshow_meta SET overlay = %s WHERE imdb_id = '%s'" % (new_value, imdb_id)
            elif tvdb_id:
                sql_update="UPDATE tvshow_meta SET overlay = %s WHERE tvdb_id = '%s'" % (new_value, tvdb_id)
        elif media_type == self.type_season:
            sql_update="UPDATE season_meta SET overlay = %s WHERE imdb_id = '%s' AND season = %s" % (new_value, imdb_id, season)        
        elif media_type == self.type_episode:
            if imdb_id:
                sql_update="UPDATE episode_meta SET overlay = %s WHERE imdb_id = '%s' AND season = %s AND episode = %s" % (new_value, imdb_id, season, episode)
            elif tvdb_id:
                sql_update="UPDATE episode_meta SET overlay = %s WHERE tvdb_id = '%s' AND season = %s AND episode = %s" % (new_value, tvdb_id, season, episode)
        else: # Something went really wrong
            return None

        addon.log('Updating watched status for type: %s, imdb id: %s, tmdb_id: %s, new value: %s' % (media_type, imdb_id, tmdb_id, new_value), 0)
        addon.log('SQL UPDATE: %s' % sql_update, 0)
        try:
            self.dbcur.execute(sql_update)
            self.dbcon.commit()
        except Exception, e:
            addon.log('************* Error attempting to update table: %s ' % e, 4)
            pass    


    def _get_watched(self, media_type, imdb_id, tmdb_id, season=''):
        '''
        Finds the watched status of the video from the cache db
        
        Args:
            media_type (str): type of video to update, 'movie', 'tvshow' or 'episode'                    
            imdb_id (str): IMDB ID
            tmdb_id (str): TMDB ID
        Kwargs:
            season (int): tv show season number    

        ''' 
        if media_type == self.type_movie:
            if imdb_id:
                sql_select="SELECT overlay FROM movie_meta WHERE imdb_id = '%s'" % imdb_id
            elif tmdb_id:
                sql_select="SELECT overlay FROM movie_meta WHERE tmdb_id = '%s'" % tmdb_id
        elif media_type == self.type_tvshow:
            sql_select="SELECT overlay FROM tvshow_meta WHERE imdb_id = '%s'" % imdb_id
        elif media_type == self.type_season:
            sql_select = "SELECT overlay FROM season_meta WHERE imdb_id = '%s' AND season = %s" % (imdb_id, season)
        
        addon.log('SQL Select: %s' % sql_select, 0)
        try:
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from %s table: %s ' % (table, e), 4)
            pass  
                    
        if matchedrow:
            return dict(matchedrow)['overlay']
        else:
            return 6

        
    def _get_watched_episode(self, meta):
        '''
        Finds the watched status of the video from the cache db
        
        Args:
            meta (dict): full data of episode                    

        '''       
        if meta['imdb_id']:
            sql_select = 'SELECT * FROM episode_meta WHERE imdb_id = "%s" AND season = %s AND episode = %s and premiered = "%s"'  % (meta['imdb_id'], meta['season'], meta['episode'], meta['episode'])
        elif meta['tvdb_id']:
            sql_select = 'SELECT * FROM episode_meta WHERE tvdb_id = "%s" AND season = %s AND episode = %s and premiered = "%s"'  % (meta['tvdb_id'], meta['season'], meta['episode'], meta['episode'])
        else:         
            sql_select = 'SELECT * FROM episode_meta WHERE title = "%s" AND season = %s AND episode = %s and premiered = "%s"'  % (self._clean_string(meta['title'].lower()), meta['season'], meta['episode'], meta['episode'])
        addon.log('Getting episode watched status', 0)
        addon.log('SQL Select: %s' % sql_select, 0)
        try:
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from episode_meta table: %s ' % e, 4)
            pass  
                   
        if matchedrow:
                return dict(matchedrow)['overlay']
        else:
            return 6


    def _find_cover(self, season, images):
        '''
        Finds the url of the banner to be used as the cover 
        from a list of images for a given season
        
        Args:
            season (str): tv show season number, number only no other characters
            images (dict): all images related
                        
        Returns:
            (str) cover_url: url of the selected image
        '''         
        cover_url = ''
        
        for image in images:
            (banner_url, banner_type, banner_season) = image
            if banner_season == season and banner_type == 'season':
                cover_url = banner_url
                break
        
        return cover_url


    def get_seasons(self, tvshowtitle, imdb_id, seasons, overlay=6):
        '''
        Requests from TVDB a list of images for a given tvshow
        and list of seasons
        
        Args:
            tvshowtitle (str): TV Show Title
            imdb_id (str): IMDB ID
            seasons (str): a list of seasons, numbers only
                        
        Returns:
            (list) list of covers found for each season
        '''     
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)
                
        coversList = []
        tvdb_id = self._get_tvdb_id(tvshowtitle, imdb_id)
        images  = None
        for season in seasons:
            meta = self._cache_lookup_season(imdb_id, tvdb_id, season)
            if meta is None:
                meta = {}
                if tvdb_id is None or tvdb_id == '':
                    meta['cover_url']=''
                elif images:
                    meta['cover_url']=self._find_cover(season, images )
                else:
                    if len(str(season)) == 4:
                        meta['cover_url']=''
                    else:
                        images = self._get_season_posters(tvdb_id, season)
                        meta['cover_url']=self._find_cover(season, images )
                        
                meta['season'] = int(season)
                meta['tvdb_id'] = tvdb_id
                meta['imdb_id'] = imdb_id
                meta['overlay'] = overlay
                meta['backdrop_url'] = self._get_tvshow_backdrops(imdb_id, tvdb_id)
                              
                #Ensure we are not sending back any None values, XBMC doesn't like them
                meta = self._remove_none_values(meta)
                                
                self._cache_save_season_meta(meta)

            #Set Watched flag
            meta['playcount'] = self.__set_playcount(meta['overlay'])
            
            coversList.append(meta)
                   
        return coversList


    def update_season(self, tvshowtitle, imdb_id, season):
        '''
        Update an individual season:
            - looks up and deletes existing entry, saving watched flag (overlay)
            - re-scans TVDB for season image
        
        Args:
            tvshowtitle (str): TV Show Title
            imdb_id (str): IMDB ID
            season (int): season number to be refreshed
                        
        Returns:
            (list) list of covers found for each season
        '''     

        #Find tvdb_id for the TVshow
        tvdb_id = self._get_tvdb_id(tvshowtitle, imdb_id)

        addon.log('---------------------------------------------------------------------------------------', 2)
        addon.log('Updating season meta data: %s IMDB: %s TVDB ID: %s SEASON: %s' % (tvshowtitle, imdb_id, tvdb_id, season), 2)

      
        if imdb_id:
            imdb_id = self._valid_imdb_id(imdb_id)
        else:
            imdb_id = ''
       
        #Lookup in cache table for existing entry
        meta = self._cache_lookup_season(imdb_id, tvdb_id, season)
        
        #We found an entry in the DB, so lets delete it
        if meta:
            overlay = meta['overlay']
            self._cache_delete_season_meta(imdb_id, tvdb_id, season)
        else:
            overlay = 6
            addon.log('No match found in cache db', 0)

        return self.get_seasons(tvshowtitle, imdb_id, season, overlay)


    def _get_tvshow_backdrops(self, imdb_id, tvdb_id):
        '''
        Gets the backdrop_url from tvshow_meta to be included with season & episode meta
        
        Args:              
            imdb_id (str): IMDB ID
            tvdb_id (str): TVDB ID

        ''' 

        sql_select = "SELECT backdrop_url FROM tvshow_meta WHERE imdb_id = '%s' AND tvdb_id = '%s'" % (imdb_id, tvdb_id)
        
        addon.log('SQL Select: %s' % sql_select, 0)
        try:
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from tvshow_meta table: %s ' % e, 4)
            pass  
                    
        if matchedrow:
            return dict(matchedrow)['backdrop_url']
        else:
            return ''


    def _get_season_posters(self, tvdb_id, season):
        tvdb = TheTVDB()
        
        try:
            images = tvdb.get_show_image_choices(tvdb_id)
        except:
            addon.log('************* Error retreiving from thetvdb.com: %s ' % e, 4)
            images = None
            pass
            
        return images
        

    def _cache_lookup_season(self, imdb_id, tvdb_id, season):
        '''
        Lookup data for a given season in the local cache DB.
        
        Args:
            imdb_id (str): IMDB ID
            tvdb_id (str): TVDB ID
            season (str): tv show season number, number only no other characters
                        
        Returns:
            (dict) meta data for a match
        '''      
        
        addon.log('Looking up season data in cache db, imdb id: %s tvdb_id: %s season: %s' % (imdb_id, tvdb_id, season), 0)
        
        if imdb_id:
            sql_select = "SELECT a.*, b.backdrop_url FROM season_meta a, tvshow_meta b WHERE a.imdb_id = '%s' AND season =%s and a.imdb_id=b.imdb_id and a.tvdb_id=b.tvdb_id"  % (imdb_id, season)
        elif tvdb_id:
            sql_select = "SELECT a.*, b.backdrop_url FROM season_meta a, tvshow_meta b WHERE a.tvdb_id = '%s' AND season =%s  and a.imdb_id=b.imdb_id and a.tvdb_id=b.tvdb_id"  % (tvdb_id, season)
        else:
            return None
            
          
        addon.log('SQL Select: %s' % sql_select, 0)
        try:
            self.dbcur.execute(sql_select)
            matchedrow = self.dbcur.fetchone()
        except Exception, e:
            addon.log('************* Error attempting to select from season_meta table: %s ' % e, 4)
            pass 
                    
        if matchedrow:
            addon.log('Found season meta information in cache table: %s' % dict(matchedrow), 0)
            return dict(matchedrow)
        else:
            return None


    def _cache_save_season_meta(self, meta):
        '''
        Save data for a given season in local cache DB.
        
        Args:
            meta (dict): full meta data for season
        '''     
        try:
            self.dbcur.execute("SELECT * FROM season_meta WHERE imdb_id = '%s' AND season ='%s' " 
                               % ( meta['imdb_id'], meta['season'] ) ) 
            matchedrow = self.dbcur.fetchone()
            if matchedrow:
                addon.log('Season matched row found, deleting table entry', 0)
                self.dbcur.execute("DELETE FROM season_meta WHERE imdb_id = '%s' AND season ='%s' " 
                                   % ( meta['imdb_id'], meta['season'] ) )
        except Exception, e:
            addon.log('************* Error attempting to delete from cache table: %s ' % e, 4)
            addon.log('Meta data: %s' % meta, 4)
            pass 
                    
        addon.log('Saving season cache information: %s' % meta, 0)
        try:
            sql_insert = self.__insert_from_dict('season_meta', 5)
            addon.log('SQL Insert: %s' % sql_insert, 0)
            self.dbcur.execute(sql_insert, (meta['imdb_id'],meta['tvdb_id'],meta['season'],
                               meta['cover_url'],meta['overlay'])
                               )
            self.dbcon.commit()
        except Exception, e:
            addon.log('************* Error attempting to insert into seasons cache table: %s ' % e, 4)
            addon.log('Meta data: %s' % meta, 4)
            pass         


    def _cache_delete_season_meta(self, imdb_id, tvdb_id, season):
        '''
        Delete meta data from SQL table
        
        Args:
            imdb_id (str): IMDB ID
            tvdb_id (str): TVDB ID
            season (int): Season #
        '''

        sql_delete = "DELETE FROM season_meta WHERE imdb_id = '%s' AND tvdb_id = '%s' and season = %s" % (imdb_id, tvdb_id, season)

        addon.log('Deleting table entry: IMDB: %s TVDB: %s Season: %s ' % (imdb_id, tvdb_id, season), 0)
        addon.log('SQL DELETE: %s' % sql_delete, 0)
        try:
            self.dbcur.execute(sql_delete)
        except Exception, e:
            addon.log('************* Error attempting to delete from season cache table: %s ' % e, 4)
            pass
