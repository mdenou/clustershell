#!/usr/bin/env python
#
# Copyright CEA/DAM/DIF (2008, 2009, 2010)
# Contributor: Henri DOREAU <henri.doreau@gmail.com>
#
# This file is part of the ClusterShell library.
#
# This software is governed by the CeCILL-C license under French law and abiding
# by the rules of distribution of free software. You can use, modify and/ or
# redistribute the software under the terms of the CeCILL-C license as
# circulated by CEA, CNRS and INRIA at the following URL
# "http://www.cecill.info".
#
# As a counterpart to the access to the source code and rights to copy, modify
# and redistribute granted by the license, users are provided only with a
# limited warranty and the software's author, the holder of the economic rights,
# and the successive licensors have only limited liability.
#
# In this respect, the user's attention is drawn to the risks associated with
# loading, using, modifying and/or developing or reproducing the software by the
# user in light of its specific status of free software, that may mean that it
# is complicated to manipulate, and that also therefore means that it is
# reserved for developers and experienced professionals having in-depth computer
# knowledge. Users are therefore encouraged to load and test the software's
# suitability as regards their requirements in conditions enabling the security
# of their systems and/or data to be ensured and, more generally, to use and
# operate it in the same conditions as regards security.
#
# The fact that you are presently reading this means that you have had knowledge
# of the CeCILL-C license and that you accept its terms.
#
# $Id$

"""
ClusterShell inter-nodes communication module

This module contains the required material for nodes to communicate between each
others whithin the propagation tree. At the highest level, messages are
instances of several classes. Then they're converted into XML to be sent over
SSH links.

In the other side, XML is parsed and new message objects are instanciated.
"""

import cPickle
import xml.sax

from xml.sax.handler import ContentHandler
from xml.sax.saxutils import XMLGenerator

from cStringIO import StringIO
from collections import deque


# GLOBAL MESSAGE TYPES
MSG_CFG = 'CFG'
MSG_CTL = 'CTL'
MSG_ACK = 'ACK'
MSG_BYE = 'BYE'
MSG_ERR = 'ERR'



class XMLMsgHandler(ContentHandler):
    """SAX handler for XML -> CSMessages instances conversion"""
    def __init__(self):
        """
        """
        ContentHandler.__init__(self)
        self.msg_queue = deque()
        self._msg_factory = CSMessageFactory()

    def startElement(self, name, attrs):
        """read a starting xml tag"""
        if name == 'message':
            self._msg_factory.new(attrs)
        else:
            self._msg_factory.update(name, attrs)

    def endElement(self, name):
        """read an ending xml tag"""
        if name == 'message':
            self.msg_queue.appendleft(self._msg_factory.deliver())

    def characters(self, content):
        """read content characters"""
        self._msg_factory.data_update(content)

    def pop_msg(self):
        """pop and return the oldest message queued"""
        if len(self.msg_queue) < 1:
            self.msg_queue.appendleft(self._msg_factory.deliver())
        return self.msg_queue.pop()

class CSMessageFactory:
    """XML -> instance deserialization class"""
    def __init__(self):
        """
        """
        # current packet under construction
        self._draft = None
        self._attr_map = None

    def new(self, attributes):
        """start a new packet construction"""
        # associative array to select to correct constructor according to the
        # message type field contained in the serialized representation
        ctors_map = {
            MSG_CFG: ConfigurationMessage,
            MSG_CTL: ControlMessage,
            MSG_ACK: ACKMessage,
            MSG_BYE: ExitMessage,
            MSG_ERR: ErrorMessage
        }
        msg_type = attributes['type']
        try:
            # select the good constructor
            ctor = ctors_map[msg_type]
        except KeyError:
            raise MessageProcessingError('Unknown message type')
        self._draft = ctor()
        # obtain expected sections map for this type of messages
        self._attr_map = self._draft.expected_sections()
        self.update('message', attributes)

    def update(self, name, attributes):
        """update the current message draft with a new section"""
        try:
            handle = self._attr_map[name]
        except KeyError:
            raise MessageProcessingError(
                'Invalid call to CSMessageFactory::update()')
        else:
            handle(attributes)

    def data_update(self, raw):
        """update the current message draft with characters"""
        self._draft.data_update(raw)

    def deliver(self):
        """release the current packet"""
        return self._draft

class MessagesProcessor:
    """abstraction layer over XML that let external modules only deal with
    messages as instances.
    """
    def __init__(self, ifile, ofile):
        """
        """
        self.exit = False
        self.name = ''
        self.parent = ''
        self._ifile = ifile
        self._ofile = ofile
        self._handler = XMLMsgHandler()
        self._parser = xml.sax.make_parser(["IncrementalParser"])
        self._parser.setContentHandler(self._handler)

    def set_ident(self, name, parent):
        """set self and parent hostnames"""
        self.name = name
        self.parent = parent

    def read_msg(self):
        """read and parse incoming messages"""
        while len(self._handler.msg_queue) < 1:
            data = self.recv()
            if not data:
                break
            self._parser.feed(data)
        return self._handler.pop_msg()

    def shutdown(self):
        """free resources and set the exit flag to True"""
        self._parser.close()
        self.exit = True

    def ack(self, msg_id):
        """acknowledge a message"""
        ack = ACKMessage()
        ack.src = self.name
        ack.dst = self.parent
        ack.ack_id = msg_id
        self.send(ack.xml())

    def send(self, raw):
        """send a raw message to output"""
        self._ofile.write(raw)

    def recv(self, size=1024):
        """read up to `size' bytes from input"""
        return self._ifile.read(size)

class CSMessage:
    """base message class"""
    _inst_counter = 0

    def __init__(self):
        """
        """
        self.type = 0
        self.src = ''
        self.dst = ''
        self.id = CSMessage._inst_counter
        CSMessage._inst_counter += 1

    def expected_sections(self):
        """return an associative array made of sections name (keys) and methods
        to handle these sections (values)
        """
        sections_map = {
            'message': self.handle_message
        }
        return sections_map

    def handle_message(self, attributes):
        """handle a "message" section"""
        try:
            self.type = attributes['type']
            self.src = attributes['src']
            self.dst = attributes['dst']
        except KeyError, k:
            raise MessageProcessingError(
                'Invalid attributes set: missing key "%s"' % k)

    def xml(self):
        """return the XML representation of the current instance"""
        raise NotImplementedError('Abstract method: subclasses must implement')

class ConfigurationMessage(CSMessage):
    """configuration propagation container"""
    def __init__(self):
        """
        """
        CSMessage.__init__(self)
        self.type = MSG_CFG
        self.data = ''

    def data_encode(self, inst):
        """serialize an instance and store the result"""
        self.data = cPickle.dumps(inst)

    def data_decode(self):
        """deserialize a previously encoded instance and return it"""
        return cPickle.loads(self.data)

    def data_update(self, raw):
        """append data to the instance (used for deserialization)"""
        # TODO : bufferize and use ''.join() for performance
        self.data += raw

    def xml(self):
        """generate XML version of a configuration message"""
        out = StringIO()
        generator = XMLGenerator(out)
        msg_attr = {
            'type': self.type,
            'src': self.src,
            'dst': self.dst,
            'id': str(self.id)
        }
        generator.startElement('message', msg_attr)
        generator.characters(self.data)
        generator.endElement('message')
        xml_msg = out.getvalue()
        out.close()
        return xml_msg

class ControlMessage(CSMessage):
    """action request"""
    def __init__(self):
        """
        """
        CSMessage.__init__(self)
        self.type = MSG_CTL
        self.action_type = ''
        self.action_target = ''
        self.params = {}

    def expected_sections(self):
        """return an associative array made of sections name (keys) and methods
        to handle these sections (values)
        """
        sections_map = CSMessage.expected_sections(self)
        sections_map['action'] = self.handle_action
        sections_map['param'] = self.handle_param
        return sections_map

    def handle_action(self, attributes):
        """handle attributes associated to an "action" section"""
        try:
            self.action_type = attributes['type']
            self.action_target = attributes['target']
        except KeyError, k:
            raise MessageProcessingError(
                'Invalid attributes set: missing key "%s"' % k)

    def handle_param(self, attributes):
        """handle attributes associated to a "param" section"""
        try:
            self.params['cmd'] = attributes['cmd']
        except KeyError, k:
            raise MessageProcessingError(
                'Invalid attributes set: missing key "%s"' % k)

    def xml(self):
        """generate XML version of a control message"""
        out = StringIO()
        generator = XMLGenerator(out)
        msg_attr = {
            'type': self.type,
            'src': self.src,
            'dst': self.dst,
            'id': str(self.id)
        }
        generator.startElement('message', msg_attr)
        action_attr = {
            'type': self.action_type,
            'target': str(self.action_target)
        }
        generator.startElement('action', action_attr)
        for k, v in self.params.iteritems():
            generator.startElement('param', {str(k): str(v)})
            generator.endElement('param')
        generator.endElement('action')
        generator.endElement('message')
        xml_msg = out.getvalue()
        out.close()
        return xml_msg

class ACKMessage(CSMessage):
    """acknowledgement message"""
    def __init__(self):
        """
        """
        CSMessage.__init__(self)
        self.type = MSG_ACK
        self.ack_id = 0
    
    def handle_message(self, attributes):
        """handle attributes associated to a "message" section"""
        CSMessage.handle_message(self, attributes)
        try:
            self.ack_id = attributes['ack']
        except KeyError, k:
            raise MessageProcessingError(
                'Invalid attributes set: missing key "%s"' % k)

    def xml(self):
        """generate XML version of an acknowledgement message"""
        out = StringIO()
        generator = XMLGenerator(out)
        msg_attr = {
            'type': self.type,
            'src': self.src,
            'dst': self.dst,
            'id': str(self.id),
            'ack': str(self.ack_id)
        }
        generator.startElement('message', msg_attr)
        xml_msg = out.getvalue()
        out.close()
        return xml_msg

class ExitMessage(CSMessage):
    """request termination"""
    def __init__(self):
        """
        """
        CSMessage.__init__(self)
        self.type = MSG_BYE

    def xml(self):
        """generate XML version of an exit request message"""
        out = StringIO()
        generator = XMLGenerator(out)
        msg_attr = {
            'type': self.type,
            'src': self.src,
            'dst': self.dst,
            'id': str(self.id)
        }
        generator.startElement('message', msg_attr)
        xml_msg = out.getvalue()
        out.close()
        return xml_msg

class ErrorMessage(CSMessage):
    """error message"""
    def __init__(self):
        """
        """
        CSMessage.__init__(self)
        self.type = MSG_ERR
        self.reason = ''
        self.id_ref = 0
    
    def handle_message(self, attributes):
        """handle attributes associated to a "message" section"""
        CSMessage.handle_message(self, attributes)
        try:
            self.reason = attributes['reason']
            self.id_ref = attributes['id_ref']
        except KeyError, k:
            raise MessageProcessingError(
                'Invalid attributes set: missing key "%s"' % k)

    def xml(self):
        """generate XML version of an error message"""
        out = StringIO()
        generator = XMLGenerator(out)
        msg_attr = {
            'type': self.type,
            'src': self.src,
            'dst': self.dst,
            'id': str(self.id),
            'reason': self.reason,
            'id_ref': str(self.id_ref)
        }
        generator.startElement('message', msg_attr)
        xml_msg = out.getvalue()
        out.close()
        return xml_msg

class MessageProcessingError(Exception):
    """base exception raised when an error occurs while processing incoming or
    outgoing messages.
    """
