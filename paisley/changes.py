# -*- Mode: Python; test-case-name: paisley.test.test_changes -*-
# vi:si:et:sw=4:sts=4:ts=4

# Copyright (c) 2011
# See LICENSE for details.

from urllib import urlencode

from twisted.internet import error, defer
from twisted.protocols import basic

from paisley.client import json


class ChangeReceiver(basic.LineReceiver):
    # figured out by checking the last two characters on actually received
    # lines
    delimiter = '\n'

    def __init__(self, notifier):
        self._notifier = notifier

    def lineReceived(self, line):
        if not line:
            return

        change = json.loads(line)

        if not 'id' in change:
            return

        self._notifier.changed(change)

    def connectionLost(self, reason):
        self._notifier.connectionLost(reason)


class ChangeListener:
    """
    I am an interface for receiving changes from a L{ChangeNotifier}.
    """

    def changed(self, change):
        """
        @type  change: dict of str -> str

        The given change was received.
        Only changes that contain an id get received.

        A change is a dictionary with:
          - id:  document id
          - seq: sequence number of change
          - changes: list of dict containing document revisions
          - deleted (optional)
        """
        pass

    def connectionLost(self, reason):
        """
        @type  reason: L{twisted.python.failure.Failure}
        """
        pass


class ChangeNotifier(object):

    def __init__(self, db, dbName, since=None):
        self._db = db
        self._dbName = dbName

        self._caches = []
        self._listeners = []
        self._prot = None

        self._since = since

        self._running = False

    def addCache(self, cache):
        self._caches.append(cache)

    def addListener(self, listener):
        self._listeners.append(listener)

    def isRunning(self):
        return self._running

    def start(self, **kwargs):
        """
        Start listening and notifying of changes.
        Separated from __init__ so you can add caches and listeners.

        By default, I will start listening from the most recent change.
        """
        assert 'feed' not in kwargs, \
            "ChangeNotifier always listens continuously."

        d = defer.succeed(None)

        def setSince(info):
            self._since = info['update_seq']

        if self._since is None:
            d.addCallback(lambda _: self._db.infoDB(self._dbName))
            d.addCallback(setSince)

        def requestChanges():
            kwargs['feed'] = 'continuous'
            kwargs['since'] = self._since
            # FIXME: str should probably be unicode, as dbName can be
            url = str(self._db.url_template %
                '/%s/_changes?%s' % (self._dbName, urlencode(kwargs)))
            return self._db.client.request('GET', url)
        d.addCallback(lambda _: requestChanges())

        def requestCb(response):
            self._prot = ChangeReceiver(self)
            response.deliverBody(self._prot)
            self._running = True
        d.addCallback(requestCb)

        def returnCb(_):
            return self._since
        d.addCallback(returnCb)
        return d

    def stop(self):
        # FIXME: this should produce a clean stop, but it does not.
        # From http://twistedmatrix.com/documents/current/web/howto/client.html
        # "If it is decided that the rest of the response body is not desired,
        # stopProducing can be used to stop delivery permanently; after this,
        # the protocol's connectionLost method will be called."
        self._running = False
        self._prot.stopProducing()

    # called by receiver

    def changed(self, change):
        seq = change.get('seq', None)
        if seq:
            self._since = seq

        for cache in self._caches:
            cache.delete(change['id'])

        for listener in self._listeners:
            listener.changed(change)

    def connectionLost(self, reason):
        # even if we asked to stop, we still get
        # a twisted.web._newclient.ResponseFailed containing
        #   twisted.internet.error.ConnectionDone
        # and
        #   twisted.web.http._DataLoss
        # If we actually asked to stop, just pass through only ConnectionDone

        # FIXME: poking at internals to get failures ? Yuck!
        from twisted.web import _newclient
        if reason.check(_newclient.ResponseFailed):
            if reason.value.reasons[0].check(error.ConnectionDone) and \
                not self.isRunning():
                reason = reason.value.reasons[0]

        self._prot = None
        self._running = False
        for listener in self._listeners:
            listener.connectionLost(reason)
