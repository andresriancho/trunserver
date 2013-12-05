from django.core.management.base import BaseCommand, CommandError
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.servers.basehttp import get_internal_wsgi_application
#from django.utils import autoreload
from trunserv import autoreload

from twisted.application import internet, service, app
from twisted.web import server, resource, wsgi
from twisted.python import threadpool, log
from twisted.internet import reactor, ssl

from optparse import make_option
import sys
import os
import re


naiveip_re = re.compile(r"""^(?:
(?P<addr>
    (?P<ipv4>\d{1,3}(?:\.\d{1,3}){3}) |         # IPv4 address
    (?P<ipv6>\[[a-fA-F0-9:]+\]) |               # IPv6 address
    (?P<fqdn>[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*) # FQDN
):)?(?P<port>\d+)$""", re.X)
DEFAULT_PORT = "8000"


class Root(resource.Resource):
    def __init__(self, wsgi_resource):
        resource.Resource.__init__(self)
        self.wsgi_resource = wsgi_resource

    def getChild(self, path, request):
        path0 = request.prepath.pop(0)
        request.postpath.insert(0, path0)
        return self.wsgi_resource


def wsgi_resource():
    pool = threadpool.ThreadPool()
    pool.start()
    # Allow Ctrl-C to get you out cleanly:
    reactor.addSystemEventTrigger('after', 'shutdown', pool.stop)
    handler = StaticFilesHandler(get_internal_wsgi_application())
    wsgi_resource = wsgi.WSGIResource(reactor, pool, handler)
    return wsgi_resource


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--noreload', action='store_false', dest='use_reloader',
                    default=True, help='Do NOT use the auto-reloader.'),
        make_option('--ssl-priv-key', action='store', dest='priv_key_file',
                    default=None, help='Private SSL key file'),
        make_option('--ssl-cert', action='store', dest='cert_file',
                    default=None, help='SSL certificate file'),
    )
    help = "Starts a Twisted Web server for development."
    args = '[optional port number, or ipaddr:port] '\
           '[--ssl-priv-key=KEY --ssl-cert=CERT]'

    # Validation is called explicitly each time the server is reloaded.
    requires_model_validation = False

    def handle(self, addrport='', *args, **options):
        #
        #    Handle bind address configuration
        #
        if not addrport:
            self.addr = ''
            self.port = DEFAULT_PORT
        else:
            m = re.match(naiveip_re, addrport)
            if m is None:
                raise CommandError('"%s" is not a valid port number '
                                   'or address:port pair.' % addrport)
            self.addr, _ipv4, _ipv6, _fqdn, self.port = m.groups()
            if not self.port.isdigit():
                raise CommandError("%r is not a valid port." % self.port)

        if not self.addr:
            self.addr = '127.0.0.1'

        #
        #    Handle SSL configuration
        #
        priv_key_file = options.get('priv_key_file', '')
        cert_file = options.get('cert_file', '')
        self.use_ssl = False
        
        if priv_key_file: 
            if not os.path.exists(priv_key_file):
                error = 'The SSL private key file "%s" does not exist'
                raise CommandError(error % priv_key_file)
            else:
                self.priv_key_file = priv_key_file
                
        if cert_file: 
            if not os.path.exists(cert_file):
                error = 'The SSL certificate file "%s" does not exist'
                raise CommandError(error % cert_file)
            else:
                self.cert_file = cert_file
                
        if (cert_file and not priv_key_file) or (priv_key_file and not cert_file):
            error = 'You need to specify both --ssl-priv-key and --ssl-cert'
            raise CommandError(error)
        
        if cert_file and priv_key_file:
            self.use_ssl = True

        self.run(*args, **options)

    def run(self, *args, **options):
        use_reloader = options.get('use_reloader', True)

        def _inner_run():
            # Initialize logging
            log.startLogging(sys.stdout)

            # Setup Twisted application
            application = service.Application('django')
            wsgi_root = wsgi_resource()
            root = Root(wsgi_root)

            main_site = server.Site(root)
            
            if self.use_ssl:
                context = ssl.DefaultOpenSSLContextFactory(self.priv_key_file,
                                                           self.cert_file)
                tcp_server = internet.SSLServer(int(self.port), main_site, context)
            else:
                tcp_server = internet.TCPServer(int(self.port), main_site)
            
            tcp_server.setServiceParent(application)

            service.IService(application).startService()
            app.startApplication(application, False)

            stop = service.IService(application).stopService
            reactor.addSystemEventTrigger('before', 'shutdown', stop)

            reactor.run()

        if use_reloader:
            try:
                autoreload.main(_inner_run)
            except TypeError:
                # autoreload was in the middle of something
                pass
        else:
            _inner_run()
