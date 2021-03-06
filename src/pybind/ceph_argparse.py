"""
Types and routines used by the ceph CLI as well as the RESTful
interface.  These have to do with querying the daemons for
command-description information, validating user command input against
those descriptions, and submitting the command to the appropriate
daemon.

Copyright (C) 2013 Inktank Storage, Inc.

This is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public
License version 2, as published by the Free Software
Foundation.  See file COPYING.
"""
import copy
import json
import os
import socket
import stat
import sys
import types
import uuid

class ArgumentError(Exception):
    """
    Something wrong with arguments
    """
    pass

class ArgumentNumber(ArgumentError):
    """
    Wrong number of a repeated argument
    """
    pass

class ArgumentFormat(ArgumentError):
    """
    Argument value has wrong format
    """
    pass

class ArgumentValid(ArgumentError):
    """
    Argument value is otherwise invalid (doesn't match choices, for instance)
    """
    pass

class ArgumentPrefix(ArgumentError):
    """
    Special for mismatched prefix; less severe, don't report by default
    """
    pass

class JsonFormat(Exception):
    """
    some syntactic or semantic issue with the JSON
    """
    pass

class CephArgtype(object):
    """
    Base class for all Ceph argument types

    Instantiating an object sets any validation parameters
    (allowable strings, numeric ranges, etc.).  The 'valid'
    method validates a string against that initialized instance,
    throwing ArgumentError if there's a problem.
    """
    def __init__(self, **kwargs):
        """
        set any per-instance validation parameters here
        from kwargs (fixed string sets, integer ranges, etc)
        """
        pass

    def valid(self, s, partial=False):
        """
        Run validation against given string s (generally one word);
        partial means to accept partial string matches (begins-with).
        If cool, set self.val to the value that should be returned
        (a copy of the input string, or a numeric or boolean interpretation
        thereof, for example), and return True
        if not, throw ArgumentError(msg-as-to-why)
        """
        self.val = s
        return True

    def __repr__(self):
        """
        return string representation of description of type.  Note,
        this is not a representation of the actual value.  Subclasses
        probably also override __str__() to give a more user-friendly
        'name/type' description for use in command format help messages.
        """
        a = ''
        if hasattr(self, 'typeargs'):
            a = self.typeargs
        return '{0}(\'{1}\')'.format(self.__class__.__name__, a)

    def __str__(self):
        """
        where __repr__ (ideally) returns a string that could be used to
        reproduce the object, __str__ returns one you'd like to see in
        print messages.  Use __str__ to format the argtype descriptor
        as it would be useful in a command usage message.
        """
        return '<{0}>'.format(self.__class__.__name__)

class CephInt(CephArgtype):
    """
    range-limited integers, [+|-][0-9]+ or 0x[0-9a-f]+
    range: list of 1 or 2 ints, [min] or [min,max]
    """
    def __init__(self, range=''):
        if range == '':
            self.range = list()
        else:
            self.range = list(range.split('|'))
            self.range = map(long, self.range)

    def valid(self, s, partial=False):
        try:
            val = long(s)
        except ValueError:
            raise ArgumentValid("{0} doesn't represent an int".format(s))
        if len(self.range) == 2:
            if val < self.range[0] or val > self.range[1]:
                raise ArgumentValid("{0} not in range {1}".format(val, self.range))
        elif len(self.range) == 1:
            if val < self.range[0]:
                raise ArgumentValid("{0} not in range {1}".format(val, self.range))
        self.val = val
        return True

    def __str__(self):
        r = ''
        if len(self.range) == 1:
            r = '[{0}-]'.format(self.range[0])
        if len(self.range) == 2:
            r = '[{0}-{1}]'.format(self.range[0], self.range[1])

        return '<int{0}>'.format(r)


class CephFloat(CephArgtype):
    """
    range-limited float type
    range: list of 1 or 2 floats, [min] or [min, max]
    """
    def __init__(self, range=''):
        if range == '':
            self.range = list()
        else:
            self.range = list(range.split('|'))
            self.range = map(float, self.range)

    def valid(self, s, partial=False):
        try:
            val = float(s)
        except ValueError:
            raise ArgumentValid("{0} doesn't represent a float".format(s))
        if len(self.range) == 2:
            if val < self.range[0] or val > self.range[1]:
                raise ArgumentValid("{0} not in range {1}".format(val, self.range))
        elif len(self.range) == 1:
            if val < self.range[0]:
                raise ArgumentValid("{0} not in range {1}".format(val, self.range))
        self.val = val
        return True

    def __str__(self):
        r = ''
        if len(self.range) == 1:
            r = '[{0}-]'.format(self.range[0])
        if len(self.range) == 2:
            r = '[{0}-{1}]'.format(self.range[0], self.range[1])
        return '<float{0}>'.format(r)

class CephString(CephArgtype):
    """
    String; pretty generic.
    """
    def __init__(self, badchars=''):
        self.badchars = badchars

    def valid(self, s, partial=False):
        for c in self.badchars:
            if c in s:
                raise ArgumentFormat("bad char {0} in {1}".format(c, s))
        self.val = s
        return True

    def __str__(self):
        b = ''
        if len(self.badchars):
            b = '(without chars in {0})'.format(self.badchars)
        return '<string{0}>'.format(b)

class CephSocketpath(CephArgtype):
    """
    Admin socket path; check that it's readable and S_ISSOCK
    """
    def valid(self, s, partial=False):
        mode = os.stat(s).st_mode
        if not stat.S_ISSOCK(mode):
            raise ArgumentValid('socket path {0} is not a socket'.format(s))
        self.val = s
        return True
    def __str__(self):
        return '<admin-socket-path>'

class CephIPAddr(CephArgtype):
    """
    IP address (v4 or v6) with optional port
    """
    def valid(self, s, partial=False):
        # parse off port, use socket to validate addr
        type = 6
        if s.startswith('['):
            type = 6
        elif s.find('.') != -1:
            type = 4
        if type == 4:
            port = s.find(':')
            if (port != -1):
                a = s[:port]
                p = s[port+1:]
                if int(p) > 65535:
                    raise ArgumentValid('{0}: invalid IPv4 port'.format(p))
            else:
                a = s
                p = None
            try:
                socket.inet_pton(socket.AF_INET, a)
            except:
                raise ArgumentValid('{0}: invalid IPv4 address'.format(a))
        else:
            # v6
            if s.startswith('['):
                end = s.find(']')
                if end == -1:
                    raise ArgumentFormat('{0} missing terminating ]'.format(s))
                if s[end+1] == ':':
                    try:
                        p = int(s[end+2])
                    except:
                        raise ArgumentValid('{0}: bad port number'.format(s))
                a = s[1:end]
            else:
                a = s
                p = None
            try:
                socket.inet_pton(socket.AF_INET6, a)
            except:
                raise ArgumentValid('{0} not valid IPv6 address'.format(s))
        if p is not None and long(p) > 65535:
            raise ArgumentValid("{0} not a valid port number".format(p))
        self.val = s
        return True

    def __str__(self):
        return '<IPaddr[:port]>'

class CephEntityAddr(CephIPAddr):
    """
    EntityAddress, that is, IP address/nonce
    """
    def valid(self, s, partial=False):
        ip, nonce = s.split('/')
        if not super(self.__class__, self).valid(ip):
            raise ArgumentValid('CephEntityAddr {0}: ip address invalid'.format(s))
        self.val = s
        return True

    def __str__(self):
        return '<EntityAddr>'

class CephPoolname(CephArgtype):
    """
    Pool name; very little utility
    """
    def __str__(self):
        return '<poolname>'

class CephObjectname(CephArgtype):
    """
    Object name.  Maybe should be combined with Pool name as they're always
    present in pairs, and then could be checked for presence
    """
    def valid(self, s, partial=False):
        self.val = s
        return True

    def __str__(self):
        return '<objectname>'

class CephPgid(CephArgtype):
    """
    pgid, in form N.xxx (N = pool number, xxx = hex pgnum)
    """
    def valid(self, s, partial=False):
        if s.find('.') == -1:
            raise ArgumentFormat('pgid has no .')
        poolid, pgnum = s.split('.')
        if poolid < 0:
            raise ArgumentFormat('pool {0} < 0'.format(poolid))
        try:
            pgnum = int(pgnum, 16)
        except:
            raise ArgumentFormat('pgnum {0} not hex integer'.format(pgnum))
        self.val = s
        return True

    def __str__(self):
        return '<pgid>'

class CephName(CephArgtype):
    """
    Name (type.id) where:
    type is osd|mon|client|mds
    id is a base10 int, if type == osd, or a string otherwise

    Also accept '*'
    """
    def valid(self, s, partial=False):
        if s == '*':
            self.val = s
            self.nametype = None
            self.nameid = None
            return True
        if s.find('.') == -1:
            raise ArgumentFormat('CephName: no . in {0}'.format(s))
        else:
            t, i = s.split('.')
            if not t in ('osd', 'mon', 'client', 'mds'):
                raise ArgumentValid('unknown type ' + self.t)
            if t == 'osd':
                if i != '*':
                    try:
                        i = int(i)
                    except:
                        raise ArgumentFormat('osd id ' + i + ' not integer')
            self.nametype = t
        self.val = s
        self.nameid = i
        return True

    def __str__(self):
        return '<name (type.id)>'

class CephOsdName(CephArgtype):
    """
    Like CephName, but specific to osds: allow <id> alone

    osd.<id>, or <id>, or *, where id is a base10 int
    """
    def valid(self, s, partial=False):
        if s == '*':
            self.val = s
            self.nametype = None
            self.nameid = None
            return True
        if s.find('.') != -1:
            t, i = s.split('.')
        else:
            t = 'osd'
            i = s
        if t != 'osd':
            raise ArgumentValid('unknown type ' + self.t)
        try:
            i = int(i)
        except:
            raise ArgumentFormat('osd id ' + i + ' not integer')
        self.nametype = t
        self.nameid = i
        self.val = i
        return True

    def __str__(self):
        return '<osdname (id|osd.id)>'

class CephChoices(CephArgtype):
    """
    Set of string literals; init with valid choices
    """
    def __init__(self, strings='', **kwargs):
        self.strings=strings.split('|')

    def valid(self, s, partial=False):
        if not partial:
            if not s in self.strings:
                # show as __str__ does: {s1|s2..}
                raise ArgumentValid("{0} not in {1}".format(s, self))
            self.val = s
            return True

        # partial
        for t in self.strings:
            if t.startswith(s):
                self.val = s
                return True
        raise ArgumentValid("{0} not in {1}".  format(s, self))

    def __str__(self):
        if len(self.strings) == 1:
            return '{0}'.format(self.strings[0])
        else:
            return '{0}'.format('|'.join(self.strings))

class CephFilepath(CephArgtype):
    """
    Openable file
    """
    def valid(self, s, partial=False):
        try:
            f = open(s, 'a+')
        except Exception as e:
            raise ArgumentValid('can\'t open {0}: {1}'.format(s, e))
        f.close()
        self.val = s
        return True

    def __str__(self):
        return '<outfilename>'

class CephFragment(CephArgtype):
    """
    'Fragment' ??? XXX
    """
    def valid(self, s, partial=False):
        if s.find('/') == -1:
            raise ArgumentFormat('{0}: no /'.format(s))
        val, bits = s.split('/')
        # XXX is this right?
        if not val.startswith('0x'):
            raise ArgumentFormat("{0} not a hex integer".format(val))
        try:
            long(val)
        except:
            raise ArgumentFormat('can\'t convert {0} to integer'.format(val))
        try:
            long(bits)
        except:
            raise ArgumentFormat('can\'t convert {0} to integer'.format(bits))
        self.val = s
        return True

    def __str__(self):
        return "<CephFS fragment ID (0xvvv/bbb)>"


class CephUUID(CephArgtype):
    """
    CephUUID: pretty self-explanatory
    """
    def valid(self, s, partial=False):
        try:
            uuid.UUID(s)
        except Exception as e:
            raise ArgumentFormat('invalid UUID {0}: {1}'.format(s, e))
        self.val = s
        return True

    def __str__(self):
        return '<uuid>'


class CephPrefix(CephArgtype):
    """
    CephPrefix: magic type for "all the first n fixed strings"
    """
    def __init__(self, prefix=''):
        self.prefix = prefix

    def valid(self, s, partial=False):
        if partial:
            if self.prefix.startswith(s):
                self.val = s
                return True
        else:
            if (s == self.prefix):
                self.val = s
                return True
        raise ArgumentPrefix("no match for {0}".format(s))

    def __str__(self):
        return self.prefix


class argdesc(object):
    """
    argdesc(typename, name='name', n=numallowed|N,
            req=False, helptext=helptext, **kwargs (type-specific))

    validation rules:
    typename: type(**kwargs) will be constructed
    later, type.valid(w) will be called with a word in that position

    name is used for parse errors and for constructing JSON output
    n is a numeric literal or 'n|N', meaning "at least one, but maybe more"
    req=False means the argument need not be present in the list
    helptext is the associated help for the command
    anything else are arguments to pass to the type constructor.

    self.instance is an instance of type t constructed with typeargs.

    valid() will later be called with input to validate against it,
    and will store the validated value in self.instance.val for extraction.
    """
    def __init__(self, t, name=None, n=1, req=True, **kwargs):
        if isinstance(t, types.StringTypes):
            self.t = CephPrefix
            self.typeargs = {'prefix':t}
            self.req = True
        else:
            self.t = t
            self.typeargs = kwargs
            self.req = bool(req == True or req == 'True')

        self.name = name
        self.N = (n in ['n', 'N'])
        if self.N:
            self.n = 1
        else:
            self.n = int(n)
        self.instance = self.t(**self.typeargs)

    def __repr__(self):
        r = 'argdesc(' + str(self.t) + ', '
        internals = ['N', 'typeargs', 'instance', 't']
        for (k,v) in self.__dict__.iteritems():
            if k.startswith('__') or k in internals:
                pass
            else:
                # undo mods above
                if k == 'n' and self.N:
                    v = 'N'
                r += '{0}={1}, '.format(k,v)
        for (k,v) in self.typeargs.iteritems():
                r += '{0}={1}, '.format(k,v)
        return r[:-2] + ')'

    def __str__(self):
        if ((self.t == CephChoices and len(self.instance.strings) == 1)
            or (self.t == CephPrefix)):
            s = '{0}'.format(str(self.instance))
        else:
            s = '{0}({1})'.format(self.name, str(self.instance))
            if self.N:
                s += ' [' + str(self.instance) + '...]'
        if not self.req:
            s = '{' + s + '}'
        return s

    def helpstr(self):
        """
        like str(), but omit parameter names (except for CephString,
        which really needs them)
        """
        if self.t == CephString:
            chunk = '<{0}>'.format(self.name)
        else:
            chunk = str(self.instance)
        s = '{0}'.format(chunk)
        if self.N:
            s += ' [' + chunk + '...]'
        if not self.req:
            s = '{' + s + '}'
        return s

def concise_sig(sig):
    """
    Return string representation of sig useful for syntax reference in help
    """
    first = True
    s = ''
    for d in sig:
        if first:
            first = False
        else:
            s += ' '
        s += d.helpstr()
    return s

def parse_funcsig(sig):
    """
    parse a single descriptor (array of strings or dicts) into a
    dict of function descriptor/validators (objects of CephXXX type)
    """
    newsig = []
    argnum = 0
    for desc in sig:
        argnum += 1
        if isinstance(desc, types.StringTypes):
            t = CephPrefix
            desc = {'type':t, 'name':'prefix', 'prefix':desc}
        else:
            # not a simple string, must be dict
            if not 'type' in desc:
                s = 'JSON descriptor {0} has no type'.format(sig)
                raise JsonFormat(s)
            # look up type string in our globals() dict; if it's an
            # object of type types.TypeType, it must be a
            # locally-defined class. otherwise, we haven't a clue.
            if desc['type'] in globals():
                t = globals()[desc['type']]
                if type(t) != types.TypeType:
                    s = 'unknown type {0}'.format(desc['type'])
                    raise JsonFormat(s)
            else:
                s = 'unknown type {0}'.format(desc['type'])
                raise JsonFormat(s)

        kwargs = dict()
        for key, val in desc.items():
            if key not in ['type', 'name', 'n', 'req']:
                kwargs[key] = val
        newsig.append(argdesc(t,
                              name=desc.get('name', None),
                              n=desc.get('n', 1),
                              req=desc.get('req', True),
                              **kwargs))
    return newsig


def parse_json_funcsigs(s):
    """
    parse_json_funcsigs(s)

    A function signature is mostly an array of argdesc; it's represented
    in JSON as
    {
      "cmd001": {"sig":[ "type": type, "name": name, "n": num, "req":true|false <other param>], "help":helptext}
       .
       .
       .
      ]

    A set of sigs is in an dict mapped by a unique number:
    {
      "cmd1": {
         "sig": ["type.. ], "help":{"text":helptext}
      }
      "cmd2"{
         "sig": [.. ], "help":{"text":helptext}
      }
    }

    Parse the string s and return an dict of dicts, keyed by opcode;
    each dict contains 'sig' with the array of descriptors, and 'help'
    with the helptext.
    """
    try:
        overall = json.loads(s)
    except Exception as e:
        print >> sys.stderr, "Couldn't parse JSON {0}: {1}".format(s, e)
        raise e
    sigdict = {}
    for cmdtag, cmd in overall.iteritems():
        helptext = cmd.get('help', 'no help available')
        try:
            sig = cmd['sig']
        except KeyError:
            s = "JSON descriptor {0} has no 'sig'".format(cmdtag)
            raise JsonFormat(s)
        newsig = parse_funcsig(sig)
        sigdict[cmdtag] = {'sig':newsig, 'helptext':helptext}
    return sigdict

def validate_one(word, desc, partial=False):
    """
    validate_one(word, desc, partial=False)

    validate word against the constructed instance of the type
    in desc.  May raise exception.  If it returns false (and doesn't
    raise an exception), desc.instance.val will
    contain the validated value (in the appropriate type).
    """
    if desc.instance.valid(word, partial):
        desc.numseen += 1
        if desc.N:
            desc.n = desc.numseen + 1
        return True
    return False

def matchnum(args, signature, partial=False):
    """
    matchnum(s, signature, partial=False)

    Returns number of arguments matched in s against signature.
    Can be used to determine most-likely command for full or partial
    matches (partial applies to string matches).
    """
    words = args[:]
    mysig = copy.deepcopy(signature)
    matchcnt = 0
    for desc in mysig:
        setattr(desc, 'numseen', 0)
        while desc.numseen < desc.n:
            # if there are no more arguments, return
            if not words:
                return matchcnt;
            word = words.pop(0)
            try:
                validate_one(word, desc, partial)
            except:
                if not desc.req:
                    # this wasn't required, so word may match the next desc
                    words.insert(0, word)
                    break
                else:
                    # it was required, and didn't match, return
                    return matchcnt
        if desc.req:
            matchcnt += 1
    return matchcnt

def validate(args, signature, partial=False):
    """
    validate(s, signature, partial=False)

    Assumes s represents a possible command input following format of
    signature.  Runs a validation; no exception means it's OK.  Return
    a dict containing all arguments named by their descriptor name
    (with duplicate args per name accumulated into a space-separated
    value).

    If partial is set, allow partial matching (with partial dict returned)
    """
    words = args[:]
    mysig = copy.deepcopy(signature)
    d = dict()
    for desc in mysig:
        setattr(desc, 'numseen', 0)
        while desc.numseen < desc.n:
            if words:
                word = words.pop(0)
            else:
                if desc.req:
                    if desc.N and desc.numseen < 1:
                        # wanted N, didn't even get 1
                        if partial:
                            return d
                        raise ArgumentNumber('saw {0} of {1}, expected at least 1'.format(desc.numseen, desc))
                    elif not desc.N and desc.numseen < desc.n:
                        # wanted n, got too few
                        if partial:
                            return d
                        raise ArgumentNumber('saw {0} of {1}, expected {2}'.format(desc.numseen, desc, desc.n))
                break
            try:
                validate_one(word, desc)
            except Exception as e:
                # not valid; if not required, just push back for the next one
                if not desc.req:
                    words.insert(0, word)
                    break
                else:
                    # hm, but it was required, so quit
                    if partial:
                        return d
                    raise e

            if desc.N:
                # value should be a list
                if desc.name in d:
                    d[desc.name] += [desc.instance.val]
                else:
                    d[desc.name] = [desc.instance.val]
            elif (desc.t == CephPrefix) and (desc.name in d):
                # value should be a space-joined concatenation
                d[desc.name] += ' ' + desc.instance.val
            else:
                # if first CephPrefix or any other type, just set it
                d[desc.name] = desc.instance.val
    return d

def validate_command(parsed_args, sigdict, args, verbose=False):
    """
    turn args into a valid dictionary ready to be sent off as JSON,
    validated against sigdict.
    parsed_args is the namespace back from argparse
    """
    found = []
    valid_dict = {}
    if args:
        # look for best match, accumulate possibles in bestcmds
        # (so we can maybe give a more-useful error message)
        best_match_cnt = 0
        bestcmds = []
        for cmdtag, cmd in sigdict.iteritems():
            sig = cmd['sig']
            matched = matchnum(args, sig, partial=True)
            if (matched > best_match_cnt):
                if verbose:
                    print >> sys.stderr, \
                        "better match: {0} > {1}: {2}:{3} ".format(matched,
                                      best_match_cnt, cmdtag, concise_sig(sig))
                best_match_cnt = matched
                bestcmds = [{cmdtag:cmd}]
            elif matched == best_match_cnt:
                if verbose:
                    print >> sys.stderr, \
                        "equal match: {0} > {1}: {2}:{3} ".format(matched,
                                      best_match_cnt, cmdtag, concise_sig(sig))
                bestcmds.append({cmdtag:cmd})

        if verbose:
            print >> sys.stderr, "bestcmds: ", bestcmds

        # for everything in bestcmds, look for a true match
        for cmdsig in bestcmds:
            for cmd in cmdsig.itervalues():
                sig = cmd['sig']
                helptext = cmd['helptext']
                try:
                    valid_dict = validate(args, sig, verbose)
                    found = sig
                    break
                except ArgumentPrefix:
                    # this means a CephPrefix type didn't match; since
                    # this is common, just eat it
                    pass
                except ArgumentError as e:
                    # prefixes matched, but some other arg didn't;
                    # this is interesting information if verbose
                    if verbose:
                        print >> sys.stderr, '{0}: invalid command'.\
                            format(' '.join(args))
                        print >> sys.stderr, '{0}'.format(e)
                        print >> sys.stderr, "did you mean {0}?\n\t{1}".\
                            format(concise_sig(sig), helptext)
                    pass

        if not found:
            print >> sys.stderr, 'no valid command found; 10 closest matches:'
            for cmdsig in bestcmds[:10]:
                for (cmdtag, cmd) in cmdsig.iteritems():
                    print >> sys.stderr, concise_sig(cmd['sig'])
            return None

        if parsed_args.output_format:
            valid_dict['format'] = parsed_args.output_format

        if parsed_args.threshold:
            valid_dict['threshold'] = parsed_args.threshold

        return valid_dict

def send_command(cluster, target=('mon', ''), cmd=[], inbuf='', timeout=0, 
                 verbose=False):
    """
    Send a command to a daemon using librados's
    mon_command, osd_command, or pg_command.  Any bulk input data
    comes in inbuf.

    Returns (ret, outbuf, outs); ret is the return code, outbuf is
    the outbl "bulk useful output" buffer, and outs is any status
    or error message (intended for stderr).

    If target is osd.N, send command to that osd (except for pgid cmds)
    """
    try:
        if target[0] == 'osd':
            osdid = target[1]

            if verbose:
                print >> sys.stderr, 'submit {0} to osd.{1}'.\
                    format(cmd, osdid)
            ret, outbuf, outs = \
                cluster.osd_command(osdid, cmd, inbuf, timeout)

        elif target[0] == 'pg':
            # leave it in cmddict for the OSD to use too
            pgid = target[1]
            if verbose:
                print >> sys.stderr, 'submit {0} for pgid {1}'.\
                    format(cmd, pgid)
            ret, outbuf, outs = \
                cluster.pg_command(pgid, cmd, inbuf, timeout)

        elif target[0] == 'mon':
            if verbose:
                print >> sys.stderr, '{0} to {1}'.\
                    format(cmd, target[0])
            if target[1] == '':
                ret, outbuf, outs = cluster.mon_command(cmd, inbuf, timeout)
            else:
                ret, outbuf, outs = cluster.mon_command(cmd, inbuf, timeout, target[1])

    except Exception as e:
        raise RuntimeError('"{0}": exception {1}'.format(cmd, e))

    return ret, outbuf, outs

def json_command(cluster, target=('mon', ''), prefix=None, argdict=None,
                 inbuf='', timeout=0, verbose=False):
    """
    Format up a JSON command and send it with send_command() above.
    Prefix may be supplied separately or in argdict.  Any bulk input
    data comes in inbuf.

    If target is osd.N, send command to that osd (except for pgid cmds)
    """
    cmddict = {}
    if prefix:
        cmddict.update({'prefix':prefix})
    if argdict:
        cmddict.update(argdict)

    # grab prefix for error messages
    prefix = cmddict['prefix']

    try:
        if target[0] == 'osd':
            osdtarg = CephName()
            osdtarget = '{0}.{1}'.format(*target)
            # prefer target from cmddict if present and valid
            if 'target' in cmddict:
                osdtarget = cmddict.pop('target')
            try:
                osdtarg.valid(osdtarget)
                target = ('osd', osdtarg.nameid)
            except:
                # use the target we were originally given
                pass

        ret, outbuf, outs = send_command(cluster, target, [json.dumps(cmddict)],
                                         inbuf, timeout, verbose)

    except Exception as e:
        raise RuntimeError('"{0}": exception {1}'.format(prefix, e))

    return ret, outbuf, outs


