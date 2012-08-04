#!/usr/bin/env python

# twisted imports
from twisted.words.protocols import irc
from twisted.internet import reactor, protocol, task
from twisted.python import log
from twisted.python.logfile import DailyLogFile
log.startLogging(DailyLogFile.fromFullPath("/var/log/titania.log"))

# system imports
import time, sys, string, os, pprint, socket
from datetime import datetime as dt
from urlparse import urlparse
from urllib import urlencode
import collections
import Queue
import base64

# interface imports
import SpaceAPIClient
import simplejson
import argparse
import oauth.oauth as oauth
from twittytwister import twitter

#Global Vars
HEARTBEAT = 1     ## Heartbeat time in seconds
STRIKER_OP = 1    ## Assume that the striker is attached to the k8055 DAC
STRIKER_TIME = 2  ## Time the striker is active (not counting RC time)

class MessageLogger:
    """
    An independent logger class (because separation of application
    and protocol logic is a good thing).
    """
    def __init__(self, file):
        self.file = file

    def log(self, message):
        """Write a message to the file."""
        timestamp = time.strftime("[%H:%M:%S]", time.localtime(time.time()))
        log.msg(message)
        self.file.write('%s %s\n' % (timestamp, message))
        self.file.flush()

    def close(self):
        self.file.close()


def convert(data):
    if isinstance(data, unicode):
        return str(data)
    elif isinstance(data, collections.Mapping):
        return dict(map(convert, data.iteritems()))
    elif isinstance(data, collections.Iterable):
        return type(data)(map(convert, data))
    else:
        return data

class IRCBot(irc.IRCClient):
    """A logging IRC bot."""

    def connectionMade(self):
        irc.IRCClient.connectionMade(self)
        self.logger = MessageLogger(open(self.factory.filename, "a"))
        self.logger.log("[connected at %s]" %
                        time.asctime(time.localtime(time.time())))

    def connectionLost(self, reason):
        irc.IRCClient.connectionLost(self, reason)
        self.logger.log("[disconnected at %s]" %
                        time.asctime(time.localtime(time.time())))
        self.logger.close()

    # Custom functions

    def logged_msg(self,target,msg):
        self.msg(target,msg)
        self.logger.log("<%s> %s" % (target,msg))

    # callbacks for events
    def signedOn(self):
        """Called when bot has succesfully signed on to server."""
        self.join(self.factory.channel)
        self.msg("Nickserv", "identify %s"%base64.b64decode(self.password))

    def joined(self, channel):
        """This will get called when the bot joins the channel."""
        self.logger.log("[I have joined %s]" % channel)
        #IRC Keepalive`
        self.startHeartbeat()
        #Custom heartbeat
        self.loopcall.start(5.0)

    def on_pm(self, user, channel, msg):
        if user not in self.space.admins:
            response = "Hello, I'm the IRC bot for %s, I'm afriad you're not allowed to do that, %s" % (self.space.name,user)
        # Allow authenticated users (see --admin flag) to tweet
        elif msg.startswith("tweet"):
            txt = "%s said: %s"%(user,string.join(msg.split()[1:]).strip())
            self.space.msg_q.add(txt,tweet=True,irc=False)
            response = "your message has been queued"
        # Allow authenticated users (see --admin flag) to tweet
        elif msg.startswith("speak"):
            txt = "%s said: %s"%(user,string.join(msg.split()[1:]).strip())
            self.space.msg_q.add(txt,tweet=False,irc=True)
            response = "your message has been queued"
        elif msg.startswith("open"):
            msg = string.join(msg.split()[1:]).strip()
            txt = "%s remotely opened the door: %s"%(user,msg)
            response = self.space.unlocking_door(msg)
            self.space.msg_q.add(txt,tweet=False,irc=True)

        else:
            response = "Successfully authenticated: %s"%(pprint.pformat(self.space.config))

        self.logged_msg(user,response)

    def on_msg(self,user,channel,cmd):
        #lose my nick, weird whitespace, and lower for ease of comp
        cmd_l =  ":".join(cmd.split(':')[1:]).strip().lower()
        if cmd_l.startswith("is the space"):
            if cmd_l.split()[3].split('?')[0] not in ["open","closed"]:
                response = "Stop being a smart arse, the space is either 'open' or 'closed'"
            else:
                delta = self.space.time_delta_string()
                response = "The space is %s, as of %s ago" % (self.space.status(),delta)
        elif cmd_l.startswith("is the door"):
            if cmd_l.split()[3].split('?')[0] not in ["open","closed"]:
                response = "Stop being a smart arse, the door is either 'open' or 'closed'"
            else:
                door = "closed" if self.space.api.doorClosedState() else "open"
                response = "The door is %s" % (door)
        elif cmd_l.startswith("what is the board"):
            response = "The Status of the board is %s"%self.space.board_status() 
        else:
            response = "Sorry, don't know what to do with that"

        msg="%s: %s"%(user,response)
        self.logged_msg(channel, msg)

    def privmsg(self, user, channel, msg):
        """This will get called when the bot receives a message."""
        user = user.split('!', 1)[0]
        self.logger.log("<%s> %s" % (user, msg))

        # Check to see if they're sending me a private message
        if channel == self.nickname:
            self.on_pm(user,channel,msg)
            return

        # Check to see if they're asking me for help
        if msg.startswith(self.nickname + ": help"):
            msg = "%s: I'm a little stupid at the minute; current commands I accept are:" % user
            self.logged_msg(channel, msg)
            msg = "%s: is the space open?" % self.nickname
            self.logged_msg(channel, msg)
            msg = "%s: what is the board status?" % self.nickname
            self.logged_msg(channel, msg)
            return

        # If its a message directed at me, deal with it
        if msg.startswith(self.nickname + ":"):
            self.on_msg(user, channel,msg)
            return


        # Otherwise check to see if it is a message directed at me
        if msg.startswith(self.nickname + ":"):
            msg = "%s: I am a log bot" % user
            msg += ", say \'%s:help\' for more information" % self.nickname
            self.logged_msg(channel, msg)
            return

    def action(self, user, channel, msg):
        """This will get called when the bot sees someone do an action."""
        user = user.split('!', 1)[0]
        self.logger.log("* %s %s" % (user, msg))

    # irc callbacks

    def irc_NICK(self, prefix, params):
        """Called when an IRC user changes their nickname."""
        old_nick = prefix.split('!')[0]
        new_nick = params[0]
        self.logger.log("%s is now known as %s" % (old_nick, new_nick))

    def userJoined(self, user, channel):
        self.logger.log('%s has joined %s' % (user, channel))

    def userLeft(self, user, channel):
        self.logger.log('%s has left %s' % (user, channel))

    def userQuit(self, user, quitMessage):
        self.logger.log('%s has quit. Reason: %s' % (user, quitMessage)) 


    # For fun, override the method that determines how a nickname is changed on
    # collisions. The default method appends an underscore.
    def alterCollidedNick(self, nickname):
        """
        Generate an altered version of a nickname that caused a collision in an
        effort to create an unused related name for subsequent registration.
        """
        return nickname + '^'

    def heartbeat(self):
        """
        Periodically executed tasks
        """
        #Launch Space heartbeat
        delta = self.space.heartbeat()
        if delta:
            self.topic("%s is %s: %s"%(self.space.name, self.space.status(), 
                                   self.space.config['url']))

class IRCBotFactory(protocol.ClientFactory):
    """A factory for IRCBots.

    A new protocol instance will be created each time we connect to the server.
    """
    def __init__(self, space):
        self.channel = space.irc_chan
        self.filename = "/var/log/%s.irc.log"%self.channel
        self.space=space

    def buildProtocol(self, addr):
        p = IRCBot()
        p.factory = self
        p.space = self.space
        self.space.irc = p
        p.loopcall = task.LoopingCall(p.heartbeat)
        p.nickname = self.space.username
        p.password = self.space.password
        p.realname = self.space.name
        log.msg("Built IRC: %s,%s"%(p.realname,p.nickname))
        return p

    def clientConnectionLost(self, connector, reason):
        """If we get disconnected, reconnect to server."""
        connector.connect()

    def clientConnectionFailed(self, connector, reason):
        log.err("connection failed: %s"%reason)
        reactor.stop()

class twitterClient():
    """
    A Twitter Client
    """
    def __init__(self,auth,params={}):
        consumer = oauth.OAuthConsumer(auth['ckey'],auth['csecret'])
        token = oauth.OAuthToken(auth['akey'], auth['asecret'])

        self.client = twitter.Twitter(consumer=consumer, token=token)
        self.params = params

    def tweet(self,msg,params={}):
        params = dict(self.params.items() + params.items())
        log.msg(self.client.update(msg,params))

class broadcast_queues():
    """
    Custom Class with multiple queues to handle async twitter/irc
    """
    def __init__(self):
        self.irc = Queue.Queue()
        self.twitter = Queue.Queue()

    def add(self,txt,irc=True,tweet=False):
        if irc:
            self.irc.put(txt)
        if tweet:
            self.twitter.put(txt)

        log.msg("Queued %s;T:%s;I:%s"%(txt,tweet,irc))

class hackerspace():
    """
    Space Interface
    """
    def __init__(self,args):
        self.admins = args.admin
        self.msg_q = broadcast_queues()

        self.enable_twitter = args.enable_twitter

        self.client_init(args)

        #overrides
        if args.chan is not None:
            log.err("Overriding chan:%s with %s due to override"%(self.irc_chan,args.chan))
            self.irc_chan="#%s"%args.chan

    def client_init(self,args):
        if hasattr(args,'auth_file'):
            auth=simplejson.load(open(args.auth_file,'r'))
            #SpaceAPI
            try:
                authpair = auth['api_authpair']
                base = auth['api_base']
            except KeyError as err:
                log.err("Invalid Auth Configuration:%s"%err)

            self.api = SpaceAPIClient.client(authpair=authpair,base=base)
            self.config = convert(self.api.spaceState())
            self.load_json_config(self.config)
            self.occupied = self.config['open']

            #Twitter
            self.password = auth['ircreg']
            self.admins += auth['admins']
            if self.enable_twitter:
                try:
                    self.twitter = twitterClient(auth)
                except Exception, err:
                    log.err('Twitter could not be loaded (IRC might fail too):%s'%err)
                    self.twitter = False
            else:
                log.err('Twitter explicitly disabled by console flag')
                self.twitter = False
        else:
            log.err('No Credentials for twitter/spaceapi loaded')

        # IRC
        self.irc_f = IRCBotFactory(self)
        reactor.connectTCP(self.irc_net,
                           6667,
                           self.irc_f)

    def load_json_config(self,conf):
        try:
            self.name = conf['space']
            if 'botname' in conf:
                self.username = conf['botname']
            else:
                self.username = re.sub(r'\s','',self.name)
            irc = urlparse(conf['contact']['irc'])
            self.irc_net = irc.netloc
            self.irc_chan = string.strip(irc.path,'/')
            log.msg("Using Net:%s Chan:%s"%(self.irc_net, self.irc_chan))
        except KeyError as err:
            log.err("Cannot Load Space Configuration: %s"%err)

    def status(self):
        return "open" if self.occupied else "closed"

    def time_delta_string(self):
        s = int(dt.strftime(dt.utcnow(),"%s")) - int(self.config['lastchange'])
        hours, remainder = divmod(s, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            delta = '%s hrs, %s mins' % (hours, minutes)
        else:
            delta = '%s mins, %s secs' % (minutes, seconds)
        return delta


    def state_changed(self):
        try:
            state = not self.api.buttonDownState()
        except ValueError as err:
            log.err("%s"%err)
            return False
        except socket.error as err:
            log.err("%s"%err)
            return False

        if self.occupied != state:
            self.occupied = state
            delta = self.time_delta_string()
            self.api.set_open_state(self.occupied)
            msg = "%s is now %s after %s" % (self.name,self.status(),delta)
            log.msg(msg)
            self.msg_q.add(msg,tweet=self.enable_twitter,irc=True)
            return True
        else:
            return False

    def heartbeat(self):
        self.state_changed()

        #Send any queued tweets and post one at a time
        if not self.msg_q.twitter.empty() and self.twitter:
            self.twitter.tweet(self.msg_q.twitter.get())
            self.msg_q.twitter.task_done()

        #pick up all queued irc queued messages from the space
        if not self.msg_q.irc.empty():
            msg=self.msg_q.irc.get_nowait()
            self.irc.logged_msg(self.irc_chan,msg)
            self.msg_q.irc.task_done()

if __name__ == '__main__':
    # initialize logging
    log.startLogging(sys.stdout)

    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('--auth_file', dest='auth_file', action='store',
                    default=None,
                        help='Twitter Authentication File')
    parser.add_argument('--admin', dest='admin', action='append',
                        default=[],
                        help='Nickname of Admin (multiple invocations allowed)')
    parser.add_argument('--chan', dest='chan', action='store',
                        default=None,
                        help='IRC Channel override')
    parser.add_argument('--disable_twitter', dest='enable_twitter', action='store_false',
                        help='Twitter override')

    args = parser.parse_args()

    if (args.auth_file is None):
        log.err("Need Auth file")
        sys.exit()

    print args
    #Create Hackerspace
    s=hackerspace(args)

    # run bot
    reactor.run()
