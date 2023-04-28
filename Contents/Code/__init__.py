import os
import re
import time
import string
import thread
import threading
import urllib
import copy
import json
from urllib2 import HTTPError
from datetime import datetime
from lxml import etree

API_KEY = ''
PLEX_HOST = ''

#this is from https://github.com/plexinc-agents/PlexThemeMusic.bundle/blob/master/Contents/Code/__init__.py
THEME_URL = 'http://tvthemes.plexapp.com/%s.mp3'
LINK_REGEX = r"https?:\/\/\w+.\w+(?:\/?\w+)? \[([^\]]+)\]"

def ValidatePrefs():
    pass

def Start():
    Log("Shoko metata agent started")
    HTTP.Headers['Accept'] = 'application/json'
    HTTP.CacheTime = 0.1 #cache, can you please go away, typically we will be requesting LOCALLY. HTTP.CacheTime
    ValidatePrefs()

def GetApiKey():
    global API_KEY

    if not API_KEY:
        data = json.dumps({
            'user': Prefs['Username'],
            'pass': Prefs['Password'] if Prefs['Password'] != None else '',
            'device': 'Shoko Metadata For Plex'
        })
        resp = HttpPost('api/auth', data)['apikey']
        Log.Debug("Got API KEY: %s" % resp)
        API_KEY = resp
        return resp

    return API_KEY

def HttpPost(url, postdata):
    myheaders = {'Content-Type': 'application/json'}
    return JSON.ObjectFromString(
        HTTP.Request('http://%s:%s/%s' % (Prefs['Hostname'], Prefs['Port'], url), headers=myheaders,
                     data=postdata).content)

def HttpReq(url, authenticate=True, retry=True):
    global API_KEY
    Log("Requesting: %s" % url)

    if authenticate:
        myheaders = {'apikey': GetApiKey()}

    try:
        return JSON.ObjectFromString(
            HTTP.Request('http://%s:%s/%s' % (Prefs['Hostname'], Prefs['Port'], url), headers=myheaders).content)
    except Exception, e:
        if not retry:
            raise e

        API_KEY = ''
        return HttpReq(url, authenticate, False)

class ShokoCommonAgent:
    def Search(self, results, media, lang, manual):
        name = media.show

        # Search for series using the name
        prelimresults = HttpReq('api/v3/Series/Search?query=%s&fuzzy=%s&limit=10' % (urllib.quote_plus(name.encode('utf8')), Prefs['Fuzzy'])) # http://127.0.0.1:8111/api/v3/Series/Search?query=Clannad&fuzzy=true&limit=10

        for index, result in enumerate(prelimresults):
            # Get series data
            series_id = result['IDs']['ID']
            series_data = {}
            series_data['shoko'] = result # Just to make it uniform across every place it's used
            series_data['anidb'] = HttpReq('api/v3/Series/%s/AniDB' % series_id)

            # Get year from air date
            airdate = try_get(series_data['anidb'], 'AirDate', None)
            year = airdate.split('-')[0] if airdate is not None else None

            score = 100 if series_data['shoko']['Name'] == name else 100 - index - int(result['Distance'] * 100)

            meta = MetadataSearchResult(str(series_id), series_data['shoko']['Name'], year, score, lang)
            results.Append(meta)

            # results.Sort('score', descending=True)

    def Update(self, metadata, media, lang, force):
        Log("update(%s)" % metadata.id)
        aid = metadata.id

        flags = 0
        flags = flags | Prefs['hideMiscTags']       << 0 #0b00001 : Hide AniDB Internal Tags
        flags = flags | Prefs['hideArtTags']        << 1 #0b00010 : Hide Art Style Tags
        flags = flags | Prefs['hideSourceTags']     << 2 #0b00100 : Hide Source Work Tags
        flags = flags | Prefs['hideUsefulMiscTags'] << 3 #0b01000 : Hide Useful Miscellaneous Tags
        flags = flags | Prefs['hideSpoilerTags']    << 4 #0b10000 : Hide Plot Spoiler Tags

        # Get series data
        series_data = {}
        series_data['shoko'] = HttpReq('api/v3/Series/%s' % aid) # http://127.0.0.1:8111/api/v3/Series/24
        series_data['anidb'] = HttpReq('api/v3/Series/%s/AniDB' % aid) # http://127.0.0.1:8111/api/v3/Series/24/AniDB

        Log('Series Title: %s' % series_data['shoko']['Name'])

        metadata.summary = summary_sanitizer(try_get(series_data['anidb'], 'Description'))
        metadata.title = series_data['shoko']['Name']
        metadata.rating = float(series_data['anidb']['Rating']['Value']/100)

        # Get air date
        airdate = try_get(series_data['anidb'], 'AirDate', None)
        if airdate is not None:
            metadata.originally_available_at = datetime.strptime(airdate, '%Y-%m-%d').date()

        # Get series tags
        series_tags = HttpReq('api/v3/Series/%s/Tags/%d' % (aid, flags)) # http://127.0.0.1:8111/api/v3/Series/24/Tags/0
        tags = [tag['Name'] for tag in series_tags]
        metadata.genres = tags

        # Get images
        images = try_get(series_data['shoko'], 'Images', {})
        self.metadata_add(metadata.banners, try_get(images, 'Banners', []))
        self.metadata_add(metadata.posters, try_get(images, 'Posters', []))
        self.metadata_add(metadata.art, try_get(images, 'Fanarts', []))

        # Get group
        groupinfo = HttpReq('api/v3/Series/%s/Group' % aid)
        metadata.collections = [groupinfo['Name']] if groupinfo['Size'] > 1 else []

        ### Generate general content ratings.
        ### VERY rough approximation to: https://www.healthychildren.org/English/family-life/Media/Pages/TV-Ratings-A-Guide-for-Parents.aspx

        if Prefs["Ratings"]:
            tags_lower = [tag.lower() for tag in tags] # Account for inconsistent capitalization of tags
            if 'kodomo' in tags_lower:
                metadata.content_rating = 'TV-Y'

            if 'mina' in tags_lower:
                metadata.content_rating = 'TV-G'

            if 'shoujo' in tags_lower:
                metadata.content_rating = 'TV-14'

            if 'shounen' in tags_lower:
                metadata.content_rating = 'TV-14'

            if 'josei' in tags_lower:
                metadata.content_rating = 'TV-14'

            if 'seinen' in tags_lower:
                metadata.content_rating = 'TV-MA'

            if 'borderline porn' in tags_lower:
                metadata.content_rating = 'TV-MA'

            if '18 restricted' in tags_lower:
                metadata.content_rating = 'X'

            Log('Assumed tv rating to be: %s' % metadata.content_rating)

        # Get cast
        cast = HttpReq('api/v3/Series/%s/Cast?roleType=Seiyuu' % aid) # http://127.0.0.1:8111/api/v3/Series/24/Cast?roleType=Seiyuu
        metadata.roles.clear()
        Log('Cast')
        for role in cast:
            meta_role = metadata.roles.new()
            meta_role.name = role['Staff']['Name']
            meta_role.role = role['Character']['Name']
            Log('%s - %s' % (meta_role.role, meta_role.name))
            image = role['Staff']['Image']
            if image:
                meta_role.photo = 'http://{host}:{port}/api/v3/Image/{source}/{type}/{id}'.format(host=Prefs['Hostname'], port=Prefs['Port'], source=image['Source'], type=image['Type'], id=image['ID'])

        # Get studio
        studio = HttpReq('api/v3/Series/%s/Cast?roleType=Studio' % aid) # http://127.0.0.1:8111/api/v3/Series/24/Cast?roleType=Studio
        studio = try_get(studio, 0, False)
        if not studio:
            studio = HttpReq('api/v3/Series/%s/Cast?roleType=Staff&roleDetails=Work' % aid) # http://127.0.0.1:8111/api/v3/Series/24/Cast?roleType=Staff&roleDetails=Work
            studio = try_get(studio, 0, False)
        if studio:
            Log('Studio: %s', studio['Staff']['Name'])
            metadata.studio = studio['Staff']['Name']

        # Get episode list using series ID
        episodes = HttpReq('api/v3/Series/%s/Episode?pageSize=0' % aid) # http://127.0.0.1:8111/api/v3/Series/212/Episode?pageSize=0

        for episode in episodes['List']:
            # Get episode data
            ep_id = episode['IDs']['ID']
            ep_data = {}
            ep_data['anidb'] = HttpReq('api/v3/Episode/%s/AniDB' % ep_id) # http://127.0.0.1:8111/api/v3/Episode/212/AniDB
            ep_data['tvdb'] = HttpReq('api/v3/Episode/%s/TvDB' % ep_id) # http://127.0.0.1:8111/api/v3/Episode/212/TvDB

            # Get episode type
            ep_type = ep_data['anidb']['Type']

            # Get season number
            season = 0
            episode_number = None
            if ep_type == 'Normal': season = 1
            elif ep_type == 'Special': season = 0
            elif ep_type == 'ThemeSong': season = -1
            elif ep_type == 'Trailer': season = -2
            elif ep_type == 'Parody': season = -3
            elif ep_type == 'Unknown': season = -4
            if not Prefs['SingleSeasonOrdering'] and len(ep_data['tvdb']) != 0:
                ep_data['tvdb'] = ep_data['tvdb'][0] # Take the first link, as explained before
                season = ep_data['tvdb']['Season']
                episode_number = ep_data['tvdb']['Number']

            if episode_number is None:
                episode_number = ep_data['anidb']['EpisodeNumber']

            Log('Season: %s', season)
            Log('Episode: %s', episode_number)

            episode_obj = metadata.seasons[season].episodes[episode_number]

            # Make a dict of language -> title for all titles in anidb data
            ep_titles = {}
            for item in ep_data['anidb']['Titles']:
                ep_titles[item['Language']] = item['Name']

            # Get episode title according to the preference
            title = None
            for lang in Prefs['EpisodeTitleLanguagePreference'].split(','):
                lang = lang.strip()
                title = try_get(ep_titles, lang.lower(), None)
                if title is not None: break
            if title is None: title = ep_titles['en'] # If not found, fallback to EN title

            # Replace Ambiguous Titles with Series Title
            SingleEntryTitles = ['Complete Movie', 'Music Video', 'OAD', 'OVA', 'Short Movie', 'TV Special', 'Web'] # AniDB titles used for single entries which are ambiguous
            if title in SingleEntryTitles:
                # Make a dict of language -> title for all series titles in anidb data
                series_titles = {}
                for item in series_data['anidb']['Titles']:
                    if item['Type'] != 'Short': # Exclude all short titles
                        series_titles.setdefault(item['Language'], item['Name']) # Use setdefault() to use the first title for each language
                
                # Get series title according to the preference
                singleTitle = title
                for lang in Prefs['EpisodeTitleLanguagePreference'].split(','):
                    lang = lang.strip()                                   
                    title = try_get(series_titles, lang.lower(), title)
                    if title is not singleTitle: break
                if title is singleTitle: # If not found, fallback to EN series title
                    title = try_get(series_titles, 'en', title)
                if title is singleTitle: # Fallback to TvDB title as a last resort
                    if try_get(ep_data['tvdb'], 'Title') != '': title = try_get(ep_data['tvdb'], 'Title')

            # TvDB episode title fallback
            if title.startswith('Episode ') and try_get(ep_data['tvdb'], 'Title') != '':
                title = try_get(ep_data['tvdb'], 'Title')

            episode_obj.title = title

            Log('Episode Title: %s', episode_obj.title)

            # Get description
            if try_get(ep_data['anidb'], 'Description') != '':
                episode_obj.summary = summary_sanitizer(try_get(ep_data['anidb'], 'Description'))
                Log('Description (AniDB): %s' % episode_obj.summary)
            elif ep_data['tvdb'] and try_get(ep_data['tvdb'], 'Description') != None: 
                episode_obj.summary = summary_sanitizer(try_get(ep_data['tvdb'], 'Description'))
                Log('Description (TvDB): %s' % episode_obj.summary)

            # Get air date
            airdate = try_get(ep_data['anidb'], 'AirDate', None)
            if airdate is not None:
                episode_obj.originally_available_at = datetime.strptime(airdate, '%Y-%m-%d').date()

            if Prefs['customThumbs']:
               self.metadata_add(episode_obj.thumbs, [try_get(try_get(ep_data['tvdb'], 0, {}), 'Thumbnail', {})])

            # Get writers (as original work)
            writers = HttpReq('api/v3/Series/%s/Cast?roleType=SourceWork' % aid) # http://127.0.0.1:8111/api/v3/Series/24/Cast?roleType=SourceWork
            writers = try_get(writers, 0, False)
            if writers:
                Log('Writers: %s', writers['Staff']['Name'])
                writer = episode_obj.writers.new()
                writer.name = writers['Staff']['Name']

            # Get directors
            directors = HttpReq('api/v3/Series/%s/Cast?roleType=Director' % aid) # http://127.0.0.1:8111/api/v3/Series/24/Cast?roleType=Director
            directors = try_get(directors, 0, False)
            if directors:
                Log('Directors: %s', directors['Staff']['Name'])
                director = episode_obj.directors.new()
                director.name = directors['Staff']['Name']

        # Set custom negative season names (To be enabled if Plex fixes blocking issue)
        # for season_num in metadata.seasons:
        #    season_title = None
        #    if season_num == '-1': season_title = 'Themes'
        #    elif season_num == '-2': season_title = 'Trailers'
        #    elif season_num == '-3': season_title = 'Parodies'
        #    elif season_num == '-4': season_title = 'Other'
        #    if int(season_num) < 0 and season_title is not None:
        #        Log('Renaming season: %s to %s' % (season_num, season_title))
        #        metadata.seasons[season_num].title = season_title

        #adapted from: https://github.com/plexinc-agents/PlexThemeMusic.bundle/blob/fb5c77a60c925dcfd60e75a945244e07ee009e7c/Contents/Code/__init__.py#L41-L45
        if Prefs["themeMusic"]:
            for tid in try_get(series_data['shoko']['IDs'],'TvDB', []):
                if THEME_URL % tid not in metadata.themes:
                    try:
                        metadata.themes[THEME_URL % tid] = Proxy.Media(HTTP.Request(THEME_URL % tid))
                        Log("added: %s" % THEME_URL % tid)
                    except:
                        Log("error adding music, probably not found")

    def metadata_add(self, meta, images):
        valid = list()
        
        art_url = '' # Declaring it inside the loop throws UnboundLocalError for some reason
        for art in images:
            try:
                art_url = '/api/v3/Image/{source}/{type}/{id}'.format(source=art['Source'], type=art['Type'], id=art['ID'])
                url = 'http://{host}:{port}{relativeURL}'.format(host=Prefs['Hostname'], port=Prefs['Port'], relativeURL=art_url)
                idx = try_get(art, 'index', 0)
                Log("[metadata_add] :: Adding metadata %s (index %d)" % (url, idx))
                meta[url] = Proxy.Media(HTTP.Request(url).content, idx)
                valid.append(url)
            except Exception as e:
                Log("[metadata_add] :: Invalid URL given (%s), skipping" % try_get(art, 'url', ''))
                Log(e)

        meta.validate_keys(valid)

        for key in meta.keys():
            if (key not in valid):
                del meta[key]

def summary_sanitizer(summary):
    if Prefs["synposisCleanLinks"]:
        summary = re.sub(LINK_REGEX, r'\1', summary)                                           # Replace links
    if Prefs["synposisCleanMiscLines"]:
        summary = re.sub(r'^(\*|--|~) .*',              "",      summary, flags=re.MULTILINE)  # Remove the line if it starts with ('* ' / '-- ' / '~ ')
    if Prefs["synposisRemoveSummary"]:
        summary = re.sub(r'\n(Source|Note|Summary):.*', "",      summary, flags=re.DOTALL)     # Remove all lines after this is seen
    if Prefs["synposisCleanMultiEmptyLines"]:
        summary = re.sub(r'\n\n+',                      r'\n\n', summary, flags=re.DOTALL)     # Condense multiple empty lines
    return summary.strip(" \n")

def try_get(arr, idx, default=""):
    try:
        return arr[idx]
    except:
        return default


class ShokoTVAgent(Agent.TV_Shows, ShokoCommonAgent):
    name, primary_provider, fallback_agent, contributes_to, accepts_from = (
        'ShokoTV', True, False, ['com.plexapp.agents.hama'],
        ['com.plexapp.agents.localmedia'])  # , 'com.plexapp.agents.opensubtitles'
    languages = [Locale.Language.English, 'fr', 'zh', 'sv', 'no', 'da', 'fi', 'nl', 'de', 'it', 'es', 'pl', 'hu', 'el',
                 'tr', 'ru', 'he', 'ja', 'pt', 'cs', 'ko', 'sl', 'hr']

    def search(self, results, media, lang, manual): self.Search(results, media, lang, manual)

    def update(self, metadata, media, lang, force): self.Update(metadata, media, lang, force)
