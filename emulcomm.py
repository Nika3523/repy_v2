"""
   Author: Justin Cappos

   Start Date: 27 June 2008

   Description:

   This is a collection of communications routines that provide a programmer 
   with a reasonable environment.   This is used by repy.py to provide a 
   highly restricted (but usable) environment.
"""

import restrictions
import socket

# Armon: Used to check if a socket is ready
import select

# socket uses getattr and setattr.   We need to make these available to it...
socket.getattr = getattr
socket.setattr = setattr


# needed to set threads for recvmess and waitforconn
import threading

# to force destruction of old sockets
import gc

# So I can exit all threads when an error occurs or do select
import harshexit

# Needed for finding out info about sockets, available interfaces, etc
import nonportable

# So I can print a clean traceback when an error happens
import tracebackrepy

# accounting
import nanny

# Used to check restrictions
import nanny_resource_limits

# give me uniqueIDs for the comminfo table
import idhelper

# for sleep
import time 

# Armon: Used for decoding the error messages
import errno

# Armon: Used for getting the constant IP values for resolving our external IP
import repy_constants 


###### Module Data

# This dictionary holds all of the open sockets, and
# is used to catalog all the used network tuples.
#
# The key to each entry is an identity tuple:
# (Type, Local IP, Local Port, Remote IP, Remote Port)
# Type is a string, either "TCP" or "UDP"
# Remote IP and Remote Port are None for listening socket
#
# The value associated with each key is a tuple:
# (lock, socket)
# The lock object is used to serialize access to the socket,
# and should be acquired before doing anything else.
# The socket is an actual Python socket object.
# 
OPEN_SOCKET_INFO = {}

# If we have a preference for an IP/Interface this flag is set to True
user_ip_interface_preferences = False

# Do we allow non-specified IPs
allow_nonspecified_ips = True

# Armon: Specified the list of allowed IP and Interfaces in order of their preference
# The basic structure is list of tuples (IP, Value), IP is True if its an IP, False if its an interface
user_specified_ip_interface_list = []

# This list caches the allowed IP's
# It is updated at the launch of repy, or by calls to getmyip and update_ip_cache
# NOTE: The loopback address 127.0.0.1 is always permitted. update_ip_cache will always add this
# if it is not specified explicitly by the user
allowediplist = []
cachelock = threading.Lock()  # This allows only a single simultaneous cache update


##### Internal Functions

# Determines if a specified IP address is allowed in the context of user settings
def ip_is_allowed(ip):
  """
  <Purpose>
    Determines if a given IP is allowed, by checking against the cached allowed IP's.
  
  <Arguments>
    ip: The IP address to search for.
  
  <Returns>
    True, if allowed. False, otherwise.
  """
  global allowediplist
  global user_ip_interface_preferences
  global allow_nonspecified_ips
  
  # If there is no preference, anything goes
  # same with allow_nonspecified_ips
  if not user_ip_interface_preferences or allow_nonspecified_ips:
    return True
  
  # Check the list of allowed IP's
  return (ip in allowediplist)


# Only appends the elem to lst if the elem is unique
def unique_append(lst, elem):
  if elem not in lst:
    lst.append(elem)
      
# This function updates the allowed IP cache
# It iterates through all possible IP's and stores ones which are bindable as part of the allowediplist
def update_ip_cache():
  global allowediplist
  global user_ip_interface_preferences
  global user_specified_ip_interface_list
  global allow_nonspecified_ips
  
  # If there is no preference, this is a no-op
  if not user_ip_interface_preferences:
    return
    
  # Acquire the lock to update the cache
  cachelock.acquire()
  
  # If there is any exception release the cachelock
  try:  
    # Stores the IP's
    allowed_list = []
  
    # Iterate through the allowed list, handle each element
    for (is_ip_addr, value) in user_specified_ip_interface_list:
      # Handle normal IP's
      if is_ip_addr:
        unique_append(allowed_list, value)
    
      # Handle interfaces
      else:
        try:
          # Get the IP's associated with the NIC
          interface_ips = nonportable.os_api.get_interface_ip_addresses(value)
          for interface_ip in interface_ips:
            unique_append(allowed_list, interface_ip)
        except:
          # Catch exceptions if the NIC does not exist
          pass
  
    # This will store all the IP's that we are able to bind to
    bindable_list = []
        
    # Try binding to every ip
    for ip in allowed_list:
      sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      try:
        sock.bind((ip,0))
      except:
        pass # Not a good ip, skip it
      else:
        bindable_list.append(ip) # This is a good ip, store it
      finally:
        sock.close()

    # Add loopback
    unique_append(bindable_list, "127.0.0.1")
  
    # Update the global cache
    allowediplist = bindable_list
  
  finally:      
    # Release the lock
    cachelock.release()
 

############## General Purpose socket functions ##############

def is_already_connected_exception(exceptionobj):
  """
  <Purpose>
    Determines if a given error number indicates that the socket
    is already connected.

  <Arguments>
    An exception object from a network call.

  <Returns>
    True if already connected, false otherwise
  """
  # Get the type
  exception_type = type(exceptionobj)

  # Only continue if the type is socket.error
  if exception_type is not socket.error:
    return False

  # Get the error number
  errnum = exceptionobj[0]

  # Store a list of error messages meaning we are connected
  connected_errors = ["EISCONN", "WSAEISCONN"]

  # Convert the errno to and error string name
  try:
    errname = errno.errorcode[errnum]
  except Exception,e:
    # The error is unknown for some reason...
    errname = None
  
  # Return if the error name is in our white list
  return (errname in connected_errors)


def is_addr_in_use_exception(exceptionobj):
  """
  <Purpose>
    Determines if a given error number indicates that the provided
    localip / localport are already bound and that the unique
    tuple is already in use.

  <Arguments>
    An exception object from a network call.

  <Returns>
    True if already in use, false otherwise
  """
  # Get the type
  exception_type = type(exceptionobj)

  # Only continue if the type is socket.error
  if exception_type is not socket.error:
    return False

  # Get the error number
  errnum = exceptionobj[0]

  # Store a list of error messages meaning we are in use
  in_use_errors = ["EADDRINUSE", "WSAEADDRINUSE"]

  # Convert the errno to and error string name
  try:
    errname = errno.errorcode[errnum]
  except Exception,e:
    # The error is unknown for some reason...
    errname = None
  
  # Return if the error name is in our white list
  return (errname in in_use_errors)


def is_recoverable_network_exception(exceptionobj):
  """
  <Purpose>
    Determines if a given error number is recoverable or fatal.

  <Arguments>
    An exception object from a network call.

  <Returns>
    True if potentially recoverable, False if fatal.
  """
  # Get the type
  exception_type = type(exceptionobj)

  # socket.timeout is recoverable always
  if exception_type == socket.timeout:
    return True

  # Only continue if the type is socket.error or select.error
  elif exception_type != socket.error and exception_type != select.error:
    return False
  
  # Get the error number
  errnum = exceptionobj[0]

  # Store a list of recoverable error numbers
  recoverable_errors = ["EINTR","EAGAIN","EBUSY","EWOULDBLOCK","ETIMEDOUT","ERESTART",
                        "WSAEINTR","WSAEWOULDBLOCK","WSAETIMEDOUT","EALREADY","WSAEALREADY",
                       "EINPROGRESS","WSAEINPROGRESS"]

  # Convert the errno to and error string name
  try:
    errname = errno.errorcode[errnum]
  except Exception,e:
    # The error is unknown for some reason...
    errname = None
  
  # Return if the error name is in our white list
  return (errname in recoverable_errors)


# Determines based on exception if the connection has been terminated
def is_terminated_connection_exception(exceptionobj):
  """
  <Purpose>
    Determines if the exception is indicated the connection is terminated.

  <Arguments>
    An exception object from a network call.

  <Returns>
    True if the connection is terminated, False otherwise.
    False means we could not determine with certainty if the socket is closed.
  """
  # Get the type
  exception_type = type(exceptionobj)

  # We only want to continue if it is socket.error or select.error
  if exception_type != socket.error and exception_type != select.error:
    return False

  # Get the error number
  errnum = exceptionobj[0]

  # Store a list of errors which indicate connection closed
  connection_closed_errors = ["EPIPE","EBADF","EBADR","ENOLINK","EBADFD","ENETRESET",
                              "ECONNRESET","WSAEBADF","WSAENOTSOCK","WSAECONNRESET",]

  # Convert the errnum to an error string
  try:
    errname = errno.errorcode[errnum]
  except:
    # The error number is not defined...
    errname = None

  # Return whether the errname is in our pre-defined list
  return (errname in connection_closed_errors)


# Armon: This is used for semantics, to determine if we have a valid IP.
def is_valid_ip_address(ipaddr):
  """
  <Purpose>
    Determines if ipaddr is a valid IP address.
    Address 0.0.0.0 is considered valid.

  <Arguments>
    ipaddr: String to check for validity. (It will check that this is a string).

  <Returns>
    True if a valid IP, False otherwise.
  """
  # Argument must be of the string type
  if not type(ipaddr) == str:
    return False

  # A valid IP should have 4 segments, explode on the period
  parts = ipaddr.split(".")

  # Check that we have 4 parts
  if len(parts) != 4:
    return False

  # Check that each segment is a number between 0 and 255 inclusively.
  for part in parts:
    # Check the length of each segment
    digits = len(part)
    if digits >= 1 and digits <= 3:
      # Attempt to convert to an integer
      try:
        number = int(part)
        if not (number >= 0 and number <= 255):
          return False

      except:
        # There was an error converting to an integer, not an IP
        return False
    else:
      return False

  # At this point, assume the IP is valid
  return True


# Armon: This is used for semantics, to determine if the given port is valid
def is_valid_network_port(port, allowzero=False):
  """
  <Purpose>
    Determines if a given network port is valid. 

  <Arguments>
    port: A numeric type (this will be checked) port number.
    allowzero: Allows 0 as a valid port if true

  <Returns>
    True if valid, False otherwise.
  """
  # Check the type is int or long
  if not (type(port) == long or type(port) == int):
    return False

  return ((allowzero and port == 0) or (port >= 1 and port <= 65535))


# Used to decide if an IP is the loopback IP or not.   This is needed for 
# accounting
def is_loopback(host):
  if not host.startswith('127.'):
    return False
  if len(host.split('.')) != 4:
    return False

  for number in host.split('.'):
    for char in number:
      if char not in '0123456789':
        return False

    try:
      if int(number) > 255 or int(number) < 0:
        return False
    except ValueError:
      return False
 
  return True


# Checks if binding to the local port is allowed
# type should be "TCP" or "UDP".
def is_allowed_localport(type, localport):
  # Switch to the proper resource
  if type == "TCP":
    resource = "connport"
  elif type == "UDP":
    resource = "messport"
  else:
    raise InternalRepyError("Bad type specified for is_allowed_localport()")

  # Check what is allowed by nanny
  allowed_ports = nanny_resource_limits.resource_limit(resource)

  # Check if the port is in the list
  return localport in allowed_ports



######################### Simple Public Functions ##########################



# Public interface
def gethostbyname(name):
  """
   <Purpose>
      Provides information about a hostname. Calls socket.gethostbyname().
      Translate a host name to IPv4 address format. The IPv4 address is
      returned as a string, such as '100.50.200.5'. If the host name is an
      IPv4 address itself it is returned unchanged.

   <Arguments>
     name:
         The host name to translate.

   <Exceptions>
     NetworkAddressError (descends from NetworkError) if the address cannot
     be resolved.

   <Side Effects>
     None.

   <Resource Consumption>
     This operation consumes network bandwidth of 4K netrecv, 1K netsend.
     (It's hard to tell how much was actually sent / received at this level.)

   <Returns>
     The IPv4 address as a string.
  """

  restrictions.assertisallowed('gethostbyname', name)

  # charge 4K for a look up...   I don't know the right number, but we should
  # charge something.   We'll always charge to the netsend interface...
  nanny.tattle_quantity('netsend', 1024) 
  nanny.tattle_quantity('netrecv', 4096)

  try:
    return socket.gethostbyname(name)
  except socket.gaierror:
    raise NetworkAddressError("The name '%s' could not be resolved." % name)
  except TypeError:
    raise ArgumentError("gethostbyname() takes a string as argument.")



# Public interface
def getmyip():
  """
   <Purpose>
      Provides the external IP of this computer.   Does some clever trickery.

   <Arguments>
      None

   <Exceptions>
      As from socket.gethostbyname_ex()

   <Side Effects>
      None.

   <Returns>
      The localhost's IP address
      python docs for socket.gethostbyname_ex()
  """

  restrictions.assertisallowed('getmyip')
  # I got some of this from: http://groups.google.com/group/comp.lang.python/browse_thread/thread/d931cdc326d7032b?hl=en
  
  # Update the cache and return the first allowed IP
  # Only if a preference is set
  if user_ip_interface_preferences:
    update_ip_cache()
    # Return the first allowed ip, there is always at least 1 element (loopback)
    return allowediplist[0]
  
  # Initialize these to None, so we can detect a failure
  myip = None
  
  # It's possible on some platforms (Windows Mobile) that the IP will be
  # 0.0.0.0 even when I have a public IP and the external IP is up. However, if
  # I get a real connection with SOCK_STREAM, then I should get the real
  # answer.
  for conn_type in [socket.SOCK_DGRAM, socket.SOCK_STREAM]:
        
    # Try each stable IP  
    for ip_addr in repy_constants.STABLE_PUBLIC_IPS:  
      try:
        # Try to resolve using the current connection type and 
        # stable IP, using port 80 since some platforms panic
        # when given 0 (FreeBSD)
        myip = get_localIP_to_remoteIP(conn_type, ip_addr, 80)
      except (socket.error, socket.timeout):
        # We can ignore any networking related errors, since we want to try 
        # the other connection types and IP addresses. If we fail,
        # we will eventually raise an exception anyways.
        pass
      else:
        # Return immediately if the IP address is good
        if myip != None and myip != '' and myip != "0.0.0.0": 
          return myip


  # Since we haven't returned yet, we must have failed.
  # Raise an exception, we must not be connected to the internet
  raise Exception("Cannot detect a connection to the Internet.")



def get_localIP_to_remoteIP(connection_type, external_ip, external_port=80):
  """
  <Purpose>
    Resolve the local ip used when connecting outbound to an external ip.
  
  <Arguments>
    connection_type:
      The type of connection to attempt. See socket.socket().
    
    external_ip:
      The external IP to attempt to connect to.
      
    external_port:
      The port on the remote host to attempt to connect to.
  
  <Exceptions>
    As with socket.socket(), socketobj.connect(), etc.
  
  <Returns>
    The locally assigned IP for the connection.
  """
  # Open a socket
  sockobj = socket.socket(socket.AF_INET, connection_type)

  try:
    sockobj.connect((external_ip, external_port))

    # Get the local connection information for this socket
    (myip, localport) = sockobj.getsockname()
      
  # Always close the socket
  finally:
    sockobj.close()
  
  return myip




###################### Shared message / connection items ###################




# Armon: How frequently should we check for the availability of the socket?
RETRY_INTERVAL = 0.2 # In seconds

# Private
def cleanup(handle):
  # Armon: lock the cleanup so that only one thread will do the cleanup, but
  # all the others will block as well
  try:
    handle_lock = comminfo[handle]['closing_lock']
  except KeyError:
    # Handle a possible race condition, the socket has already been cleaned up.
    return
  
  # Acquire the lock       
  handle_lock.acquire()

  # if it's in the table then remove the entry and tattle...
  try:
    if handle in comminfo:
      # Armon: Shutdown the socket for writing prior to close
      # to unblock any threads that are writing
      try:
        comminfo[handle]['socket'].shutdown(socket.SHUT_WR)
      except:
        pass

      try:
        comminfo[handle]['socket'].close()
      except:
        pass
      
      info = comminfo[handle]  # Store the info

      if info['outgoing']:
        nanny.tattle_remove_item('outsockets', handle)
      else:
        nanny.tattle_remove_item('insockets', handle)
    
        # Armon: Block while the socket is not yet cleaned up
        # Get the socket info
        ip = info['localip']
        port = info['localport']
        socketType = info['type']
        tcp = (socketType == 'TCP') # Check if this is a TCP typed connection
    
        # Loop until the socket no longer exists
        # BUG: There exists a potential race condition here. The problem is that
        # the socket may be cleaned up and then before we are able to check for it again
        # another process binds to the ip/port we are checking. This would cause us to detect
        # the socket from the other process and we would block indefinately while that socket
        # is open.
        while nonportable.os_api.exists_listening_network_socket(ip,port, tcp):
          time.sleep(RETRY_INTERVAL)
      
      # Delete the entry last, so that other stopcomm operations will block
      del comminfo[handle]
    
  finally:
    # Always release the lock
    handle_lock.release()



####################### Message sending #############################



# Public interface!!!
def sendmessage(destip, destport, message, localip, localport):
  """
   <Purpose>
      Send a message to a host / port

   <Arguments>
      destip:
         The host to send a message to
      destport:
         The port to send the message to
      message:
         The message to send
      localhost:
         The local IP to send the message from 
      localport:
         The local port to send the message from

   <Exceptions>
      AddressBindingError (descends NetworkError) when the local IP isn't
        a local IP.

      ResourceForbiddenError (descends ResourceException?) when the local
        port isn't allowed

      RepyArgumentError when the local IP and port aren't valid types
        or values

   <Side Effects>
      None.

   <Resource Consumption>
      This operation consumes 64 bytes + number of bytes of the message that
      were transmitted. This requires that the localport is allowed.

   <Returns>
      The number of bytes sent on success
  """

  # Check that if either localip or local port is specified, that both are
  if localport is None or localip is None:
    raise RepyArgumentError("Localip and localport must be specified.")
  if type(destip) is not str or type(destport) is not int or type(message) \
      is not str or type(localip) is not str or type(localport) is not int:
        raise RepyArgumentError("Invalid type of one or more arguments " + \
            "to sendmessage().")

  if not localip or localip == '0.0.0.0':
    raise RepyArgumentError("Can only bind to a single local ip.")
# JAC: removed since this breaks semantics
#  else:
#    if not is_valid_ip_address(localip):
#      raise Exception("Local IP address is invalid.")

# JAC: removed since this breaks semantics
#  if not is_valid_ip_address(destip):
#    raise Exception("Destination host IP address is invalid.")
  
  if not is_valid_network_port(destport):
    raise RepyArgumentError("Destination port number must be an " + \
        "integer, between 1 and 65535.")

  if not is_valid_network_port(localport, True):
    raise RepyArgumentError("Local port number must be an integer, " + \
        "between 1 and 65535.")

  try:
    restrictions.assertisallowed('sendmess', destip, destport, message, \
        localip, localport)
  except Exception, e:
    raise ResourceForbiddenError(str(e))

  if localport:
    nanny.tattle_check('messport', localport)

  # Armon: Check if the specified local ip is allowed
  # this check only makes sense if the localip is specified
  if localip and not ip_is_allowed(localip):
    raise ResourceForbiddenError("IP '" + str(localip) + "' is not allowed.")
  
  # If there is a preference, but no localip, then get one
  elif user_ip_interface_preferences and not localip:
    # Use whatever getmyip returns
    localip = getmyip()

  # this is used to track errors when trying to resend data
  firsterror = None

  if localip and localport:
    # let's see if the socket already exists...
    commtableentry, commhandle = find_tip_entry('UDP', localip, localport)
  else:
    # no, we'll skip
    commhandle = None

  # yes it does! let's use the existing socket
  if commhandle:

    # block in case we're oversubscribed
    if is_loopback(destip):
      nanny.tattle_quantity('loopsend', 0)
    else:
      nanny.tattle_quantity('netsend', 0)

    # try to send using this socket
    try:
      bytessent = commtableentry['socket'].sendto(message, \
          (destip, destport))
    except socket.error, e:
      # we're going to save this error in case we also get an error below.
      # This is likely to be the error we actually want to raise
      firsterror = e
      # should I really fall through here?
    else:
      # send succeeded, let's wait and return
      if is_loopback(destip):
        nanny.tattle_quantity('loopsend', 64 + bytessent)
      else:
        nanny.tattle_quantity('netsend', 64 + bytessent)
      return bytessent
  

  # open a new socket
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
 

  try:
    if localip:
      try:
        s.bind((localip,localport))
      except socket.error, e:
        if firsterror:
          raise AddressBindingError(str(firsterror))
        raise AddressBindingError(str(e))

    # wait if already oversubscribed
    if is_loopback(destip):
      nanny.tattle_quantity('loopsend', 0)
    else:
      nanny.tattle_quantity('netsend', 0)

    bytessent = s.sendto(message, (destip, destport))

    if is_loopback(destip):
      nanny.tattle_quantity('loopsend', 64 + bytessent)
    else:
      nanny.tattle_quantity('netsend', 64 + bytessent)

    return bytessent

  finally:
    # close no matter what
    try:
      s.close()
    except:
      pass






# Public interface!!!
def listenformessage(localip, localport):
  """
    <Purpose>
        Sets up a udpserversocket to receive incoming UDP messages.

    <Arguments>
        localip:
            The local IP to register the handler on.
        localport:
            The port to listen on.

    <Exceptions>
        PortInUseException (descends NetworkError) if the port cannot be
        listened on because some other process on the system is listening on
        it.

        PortInUseException if there is already a udpserversocket with the same
        IP and port.

        RepyArgumentError if the port number or ip is wrong type or obviously
        invalid.

        AddressBindingError (descends NetworkError) if the IP address isn't a
        local IP.

        ResourceForbiddenError if the port is restricted.

        SocketWouldBlockException if the call would block.

    <Side Effects>
        Prevents other udpserversockets from using this port / IP

    <Resource Consumption>
        This operation consumes an insocket and requires that the provided messport is allowed.

    <Returns>
        The udpserversocket.

  """
  if not localip or localip == '0.0.0.0':
    raise RepyArgumentError("Must specify a local IP address")

# JAC: removed since this breaks semantics
#  if not is_valid_ip_address(localip):
#    raise Exception("Local IP address is invalid.")

  if not is_valid_network_port(localport):
    raise RepyArgumentError("Local port number must be an integer, " + \
        "between 1 and 65535.")

  nanny.tattle_check('messport', localport)
  
  # Armon: Check if the specified local ip is allowed
  if not ip_is_allowed(localip):
    raise PortRestrictedException("IP '" + localip + "' is not allowed.")
  
  # Armon: Generate the new handle since we need it 
  # to replace the old handle if it exists
  handle = generate_commhandle()

  # check if I'm already listening on this port / ip
  # NOTE: I check as though there might be a socket open that is sending a
  # message.   This is nonsense since sendmess doesn't result in a socket 
  # persisting.   This is done so that if sockets for sendmess are cached 
  # later (as seems likely) the resulting code will not break.
  oldhandle = find_tipo_commhandle('UDP', localip, localport, False)
  if oldhandle:
    # if it was already there, update the function and return
    raise PortInUseException("udpserversocket for this (ip, port) " + \
        "already exists")
    
  # we'll need to add it, so add a socket...
  nanny.tattle_add_item('insockets', handle)

  # get the socket
  try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((localip, localport))

    nonportable.preparesocket(s)
  except:
    try:
      s.close()
    except:
      pass
    nanny.tattle_remove_item('insockets', handle)
    raise

  # set up our table entry
  comminfo[handle] = {'type': 'UDP', 'localip': localip, \
      'localport': localport, 'socket': s, 'outgoing': False, \
      'closing_lock': threading.Lock()}

  # start the selector if it's not running already
  check_selector()

  return udpserversocket(handle)




####################### Connection oriented #############################



# Public interface!!!
def openconn(desthost, destport,localip=None, localport=None,timeout=None):
  """
   <Purpose>
      Opens a connection, returning a socket-like object

   <Arguments>
      desthost:
         The host to open communcations with
      destport:
         The port to use for communication
      localip (optional):
         The local ip to use for the communication
      localport (optional):
         The local port to use for communication (0 for a random port)
      timeout (optional):
         The maximum amount of time to wait to connect

   <Exceptions>
      As from socket.connect, etc.

   <Side Effects>
      None.

   <Returns>
      A socket-like object that can be used for communication.   Use send, 
      recv, and close just like you would an actual socket object in python.
  """

  # Set a default timeout of 5 seconds if none is specified.
  if timeout is None:
    timeout = 5.0

  # Check that both localip and localport are given if either is specified
  if localip != None and localport == None or localport != None and localip == None:
    raise Exception("Localip and localport must be specified simultaneously.")

  # Set the default value of localip
  if not localip or localip == '0.0.0.0':
    localip = None
#  else:
# JAC: removed since this breaks semantics
    # Check that the localip is valid if given
#    if not is_valid_ip_address(localip):
#      raise Exception("Local IP address is invalid.")

  # Assign the default value of localport if none is given.
  if localport == None:
    localport = 0
 
# JAC: removed since this breaks semantics
  # Check the remote IP for validity
#  if not is_valid_ip_address(desthost):
#    raise Exception("Destination host IP address is invalid.")

  if not is_valid_network_port(destport):
    raise Exception("Destination port number must be an integer, between 1 and 65535.")

  # Allow the localport to be 0, which is the default.
  if not is_valid_network_port(localport, True):
    raise Exception("Local port number must be an integer, between 1 and 65535.")

  # Check that the timeout is a number, greater than 0
  if not (type(timeout) == float or type(timeout) == int or type(timeout) == long) or timeout <= 0.0:
    raise Exception("Timeout parameter must be a numeric value greater than 0.")

  # Armon: Check if the specified local ip is allowed
  # this check only makes sense if the localip is specified
  if localip and not ip_is_allowed(localip):
    raise Exception, "IP '"+str(localip)+"' is not allowed."

  # If there is a preference, but no localip, then get one
  elif user_ip_interface_preferences and not localip:
    # Use whatever getmyip returns
    localip = getmyip()

  restrictions.assertisallowed('openconn',desthost,destport,localip,localport)
  
  # Get our start time
  starttime = nonportable.getruntime()

  # Armon: Check for any pre-existing sockets. If they are being closed, wait for them.
  # This will also serve to check if repy has a pre-existing socket open on this same tuple
  exists = True
  while exists and nonportable.getruntime() - starttime < timeout:
    # Update the status
    (exists, status) = nonportable.os_api.exists_outgoing_network_socket(localip,localport,desthost,destport)
    if exists:
      # Check the socket state
      if "ESTABLISH" in status or "CLOSE_WAIT" in status:
        # Check if the socket is from this repy vessel
        handle = find_outgoing_tcp_commhandle(localip, localport, desthost, destport)
        
        message = "Network socket is in use by an external process!"
        if handle != None:
          message = " Duplicate handle exists with name: "+str(handle)
        
        raise Exception, message
      else:
        # Wait for socket cleanup
        time.sleep(RETRY_INTERVAL)
  else:
    # Check if a socket exists still and we timed out
    if exists:
      raise Exception, "Timed out checking for socket cleanup!"
        

  if localport:
    nanny.tattle_check('connport',localport)

  handle = generate_commhandle()

  # If allocation of an outsocket fails, we garbage collect and try again
  # -- this forces destruction of unreferenced objects, which is how we
  # free resources.
  try:
    nanny.tattle_add_item('outsockets',handle)
  except:
    gc.collect()
    nanny.tattle_add_item('outsockets',handle)

  
  try:
    s = get_real_socket(localip,localport)

  
    # add the socket to the comminfo table
    comminfo[handle] = {'type':'TCP','remotehost':None, 'remoteport':None,'localip':localip,'localport':localport,'socket':s, 'outgoing':True, 'closing_lock':threading.Lock()}
  except:
    # the socket wasn't passed to the user prog...
    nanny.tattle_remove_item('outsockets',handle)
    raise


  try:
    thissock = emulated_socket(handle)
    # We set a timeout before we connect.  This allows us to timeout slow 
    # connections...
    oldtimeout = comminfo[handle]['socket'].gettimeout()
 
    # Set the new timeout
    comminfo[handle]['socket'].settimeout(timeout)

    # Store exceptions until we exit the loop, default to timed out
    # in case we are given a very small timeout
    connect_exception = Exception("Connection timed out!")

    # Ignore errors and retry if we have not yet reached the timeout
    while nonportable.getruntime() - starttime < timeout:
      try:
        comminfo[handle]['socket'].connect((desthost,destport))
        break
      except Exception,e:
        # Check if the socket is already connected (EISCONN or WSAEISCONN)
        if is_already_connected_exception(e):
          break

        # Check if this is recoverable, only continue if it is
        elif not is_recoverable_network_exception(e):
          raise

        else:
          # Store the exception
          connect_exception = e

        # Sleep a bit, avoid excessive iterations of the loop
        time.sleep(0.2)
    else:
      # Raise any exception that was raised
      if connect_exception != None:
        raise connect_exception

    comminfo[handle]['remotehost']=desthost
    comminfo[handle]['remoteport']=destport
  
  except:
    cleanup(handle)
    raise
  else:
    # and restore the old timeout...
    comminfo[handle]['socket'].settimeout(oldtimeout)

  return thissock




def listenforconnection(localip, localport):
  """
  <Purpose>
    Sets up a TCPServerSocket to recieve incoming TCP connections. 

  <Arguments>
    localip:
        The local IP to listen on
    localport:
        The local port to listen on

  <Exceptions>
    Raises PortInUseError if another TCPServerSocket or process has bound
    to the provided localip and localport.

    Raises RepyArgumentError if the localip or localport are invalid
    Raises ResourceForbiddenError if the ip or port is not allowed.
    Raises AddressBindingError if the IP address isn't a local ip.

  <Side Effects>
    The IP / Port combination cannot be used until the TCPServerSocket
    is closed.

  <Resource Consumption>
    Uses an insocket for the TCPServerSocket.

  <Returns>
    A TCPServerSocket object.
  """
  # Check the input arguments (type)
  if type(localip) is not str:
    raise RepyArgumentError("Provided localip must be a string!")

  if type(localport) is not int:
    raise RepyArgumentError("Provided localport must be a int!")

  # Check the input arguments (sanity)
  if not is_valid_ip_address(localip):
    raise RepyArgumentError("Provided localip is not valid!")

  if not is_valid_network_port(localport):
    raise RepyArgumentError("Provided localport is not valid!")

  # Check the input arguments (permission)
  if not ip_is_allowed(localip):
    raise ResourceForbiddenError("Provided localip is not allowed!")

  if not is_allowed_localport("TCP", localport):
    raise ResourceForbiddenError("Provided localport is not allowed!")

  # Check if the tuple is in use
  identity = ("TCP", localip, localport, None, None)
  if identity in OPEN_SOCKET_INFO:
    raise PortInUseError("The provided localip and localport are already in use!")

  # Check if the localip is valid
  update_ip_cache()
  if localip not in allowediplist:
    raise AddressBindingError("The provided localip is not a local IP!")


  # Register this identity as an insocket
  nanny.tattle_add_item('insockets',identity)

  try:
    # Get the socket
    sock = get_real_socket(localip,localport)

    # NOTE: Should this be anything other than a hardcoded number?
    sock.listen(5)
  except Exception, e:
    nanny.tattle_remove_item('insockets',identity)

    # Check if this an already in use error
    if is_addr_in_use_exception(e):
      raise PortInUseError("Provided Local IP and Local Port is already in use!")
    
    # Unknown error...
    else:
      raise

  # Create entry with a lock and the socket object
  OPEN_SOCKET_INFO[identity] = (threading.Lock(), sock)

  # Create a TCPServerSocket
  server_sock = TCPServerSocket(identity)

  # Return the TCPServerSocket
  return server_sock




# Private method to create a TCP socket and bind
# to a localip and localport.
# 
# The socket is automatically set to non-blocking mode
def get_real_socket(localip, localport):
  # Create the TCP socket
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

  # Reuse the socket if it's "pseudo-availible"
  s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

  if localip and localport:
    try:
      s.bind((localip,localport))
    except: # Raise the exception un-tainted:w
      # don't leak sockets
      s.close()
      raise

  # Make the socket non-blocking
  s.setblocking(0)

  return s


# Checks if the given real socket would block
def socket_state(realsock, waitfor="rw", timeout=0.0):
  """
  <Purpose>
    Checks if the given socket would block on a send() or recv().
    In the case of a listening socket, read_will_block equates to
    accept_will_block.

  <Arguments>
    realsock:
              A real socket.socket() object to check for.

    waitfor:
              An optional specifier of what to wait for. "r" for read only, "w" for write only,
              and "rw" for read or write. E.g. if timeout is 10, and wait is "r", this will block
              for up to 10 seconds until read_will_block is false. If you specify "r", then
              write_will_block is always true, and if you specify "w" then read_will_block is
              always true.

    timeout:
              An optional timeout to wait for the socket to be read or write ready.

  <Returns>
    A tuple, (read_will_block, write_will_block).

  <Exceptions>
    As with select.select(). Probably best to wrap this with is_recoverable_network_exception
    and is_terminated_connection_exception. Throws an exception if waitfor is not in ["r","w","rw"]
  """
  # Check that waitfor is valid
  if waitfor not in ["rw","r","w"]:
    raise Exception, "Illegal waitfor argument!"

  # Array to hold the socket
  sock_array = [realsock]

  # Generate the read/write arrays
  read_array = []
  if "r" in waitfor:
    read_array = sock_array

  write_array = []
  if "w" in waitfor:
    write_array = sock_array

  # Call select()
  (readable, writeable, exception) = select.select(read_array,write_array,sock_array,timeout)

  # If the socket is in the exception list, then assume its both read and writable
  if (realsock in exception):
    return (False, False)

  # Return normally then
  return (realsock not in readable, realsock not in writeable)


##### Class Definitions

# Public.   We pass these to the users for communication purposes
class emulated_socket:
  # This is an index into the comminfo table...

  commid = 0

  def __init__(self, handle):
    self.commid = handle

    # Armon: Get the real socket
    try:
      realsocket = comminfo[handle]['socket']

    # Shouldn't happen because my caller should create the table entry first
    except KeyError:
      raise Exception, "Internal Error. No table entry for new socket!"

    # Make the socket non-blocking
    realsocket.setblocking(0)

    try:
      # Store the send buffer size.   We'll send less than this to avoid a bug
      comminfo[handle]['sendbuffersize'] = realsocket.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)

    # Really shouldn't happen.   We just checked!
    except KeyError:
      raise Exception, "Internal Error. No table entry when looking up sendbuffersize for new socket!"


    return None 




  def close(self):
    """
      <Purpose>
        Closes a socket.   Pending remote recv() calls will return with the 
        remaining information.   Local recv / send calls will fail after this.

      <Arguments>
        None

      <Exceptions>
        None

      <Side Effects>
        Pending local recv calls will either return or have an exception.

      <Returns>
        True if this is the first close call to this socket, False otherwise.
    """
    # prevent TOCTOU race with client changing the object's properties
    mycommid = self.commid
    restrictions.assertisallowed('socket.close')
    
    # Armon: Semantic update, return whatever stopcomm does.
    # This will result in socket.close() returning a True/False indicator
    return stopcomm(mycommid)



  def recv(self,bytes):
    """
      <Purpose>
        Receives data from a socket.   It may receive fewer bytes than 
        requested.   

      <Arguments>
        bytes: 
           The maximum number of bytes to read.   

      <Exceptions>
        Exception if the socket is closed either locally or remotely.

      <Side Effects>
        This call will block the thread until the other side calls send.

      <Returns>
        The data received from the socket (as a string).   If '' is returned,
        the other side has closed the socket and no more data will arrive.
    """
    # prevent TOCTOU race with client changing the object's properties
    mycommid = self.commid
    restrictions.assertisallowed('socket.recv',bytes)

    # I set this here so that I don't screw up accounting with a keyerror later
    try:
      this_is_loopback = is_loopback(comminfo[mycommid]['remotehost'])
    # they likely closed the connection
    except KeyError:
      raise Exception, "Socket closed"

    # wait if already oversubscribed
    if this_is_loopback:
      nanny.tattle_quantity('looprecv',0)
    else:
      nanny.tattle_quantity('netrecv',0)

    datarecvd = 0
    # loop until we recv the information (looping is needed for Windows)
    while True:
      try:
        # the timeout is needed so that if the socket is closed in another 
        # thread, we notice it
        # BUG: What should the timeout be?   What is the right value?
        #comminfo[mycommid]['socket'].settimeout(0.2)
        
        # Armon: Get the real socket
        realsocket = comminfo[mycommid]['socket']
	
        # Check if the socket is ready for reading
        (read_will_block, write_will_block) = socket_state(realsocket, "r", 0.2)	
        if not read_will_block:
          datarecvd = realsocket.recv(bytes)
          break

      # they likely closed the connection
      except KeyError:
        raise Exception, "Socket closed"

      # Catch all other exceptions, check if they are recoverable
      except Exception, e:
        # Check if this error is recoverable
        if is_recoverable_network_exception(e):
          continue

        # Otherwise, raise the exception
        else:
          # Check if this is a connection termination
          if is_terminated_connection_exception(e):
            raise Exception("Socket closed")
          else:
            raise

    # Armon: Calculate the length of the data
    data_length = len(datarecvd)
    
    # Raise an exception if there was no data
    if data_length == 0:
      raise Exception("Socket closed")

    # do accounting here...
    if this_is_loopback:
      nanny.tattle_quantity('looprecv',data_length)
    else:
      nanny.tattle_quantity('netrecv',data_length)

    return datarecvd



  def send(self,message):
    """
      <Purpose>
        Sends data on a socket.   It may send fewer bytes than requested.   

      <Arguments>
        message:
          The string to send.

      <Exceptions>
        Exception if the socket is closed either locally or remotely.

      <Side Effects>
        This call may block the thread until the other side calls recv.

      <Returns>
        The number of bytes sent.   Be sure not to assume this is always the 
        complete amount!
    """
    # prevent TOCTOU race with client changing the object's properties
    mycommid = self.commid
    restrictions.assertisallowed('socket.send',message)

    # I factor this out because we must do the accounting at the bottom of this
    # function and I want to make sure we account properly even if they close 
    # the socket right after their data is sent
    try:
      this_is_loopback = is_loopback(comminfo[mycommid]['remotehost'])
    except KeyError:
      raise Exception, "Socket closed!"

    # wait if already oversubscribed
    if this_is_loopback:
      nanny.tattle_quantity('loopsend',0)
    else:
      nanny.tattle_quantity('netsend',0)

    try:
      # Trim the message size to be less than the sendbuffersize.
      # This is a fix for http://support.microsoft.com/kb/823764
      message = message[:comminfo[mycommid]['sendbuffersize']-1]
    except KeyError:
      raise Exception, "Socket closed!"

    # loop until we send the information (looping is needed for Windows)
    while True:
      try:
        # Armon: Get the real socket
        realsocket = comminfo[mycommid]['socket']
	
        # Check if the socket is ready for writing, wait 0.2 seconds
        (read_will_block, write_will_block) = socket_state(realsocket, "w", 0.2)
        if not write_will_block:
          bytessent = realsocket.send(message)
          break
      
      except KeyError:
        raise Exception, "Socket closed"

      except Exception,e:
        # Determine if the exception is fatal
        if is_recoverable_network_exception(e):
          continue
        else:
          # Check if this is a conn. term., and give a more specific exception.
          if is_terminated_connection_exception(e):
            raise Exception("Socket closed")
          else:
            raise

    if this_is_loopback:
      nanny.tattle_quantity('loopsend',bytessent)
    else:
      nanny.tattle_quantity('netsend',bytessent)

    return bytessent


  # Checks if socket read/write operations will block
  def willblock(self):
    """
    <Purpose>
      Determines if a socket would block if send() or recv() was called.

    <Exceptions>
      Socket Closed if the socket has been closed.

    <Returns>
      A tuple, (recv_will_block, send_will_block) both are boolean values.

    """

    try:
      # Get the real socket
      realsocket = comminfo[self.commid]['socket']

      # Call into socket_state with no timout to return instantly
      return socket_state(realsocket)
    
    # The socket is closed or in the process of being closed...
    except KeyError:
      raise Exception, "Socket closed"

    except Exception, e:
      # Determine if the socket is closed
      if is_terminated_connection_exception(e):
        raise Exception("Socket closed")
      
      # Otherwise raise whatever we have
      else:
        raise



  def __del__(self):
    cleanup(self.commid)

# End of emulated_socket class


# Public: Class the behaves represents a listening UDP socket.
class udpserversocket:

  # UDP listening socket interface
  def __init__(self, handle):
    self._commid = handle
    self._closed = False



  def getmessage(self):
    """
    <Purpose>
        Obtains an incoming message that was sent to an IP and port.

    <Arguments>
        None.

    <Exceptions>
        LocalIPChanged if the local IP address has changed and the
        udpserversocket is invalid

        PortRestrictedException if the port number is no longer allowed.

        SocketClosedLocal if udpserversocket.close() was called.

    <Side Effects>
        None

    <Resource Consumption>
        This operation consumes 64 + size of message bytes of netrecv

    <Returns>
        A tuple consisting of the remote IP, remote port, and message.

    """

    if self._closed:
      raise SocketClosedLocal("getmessage() was called on a closed " + \
          "udpserversocket.")

    mycommid = self._commid
    socketinfo = comminfo[mycommid]
    s = socketinfo['socket']

    update_ip_cache()
    if socketinfo['localip'] not in allowediplist and \
       not is_loopback(socketinfo['localip']):
      raise LocalIPChanged("The local ip " + socketinfo['localip'] + \
          " is no longer present on a system interface.")

    # Wait if we're oversubscribed.
    if is_loopback(socketinfo['localip']):
      nanny.tattle_quantity('looprecv', 0)
    else:
      nanny.tattle_quantity('netrecv', 0)

    data, addr = s.recvfrom(4096)

    # Report resource consumption:
    if is_loopback(socketinfo['localip']):
      nanny.tattle_quantity('looprecv', 64 + len(data))
    else:
      nanny.tattle_quantity('netrecv', 64 + len(data))

    return (addr[0], addr[1], data)



  def close(self):
    """
    <Purpose>
        Closes a socket that is listening for messages.

    <Arguments>
        None.

    <Exceptions>
        None.

    <Side Effects>
        The IP address and port can be reused by other udpserversockets after
        this.

    <Resource Consumption>
        If applicable, this operation stops consuming the corresponding
        insocket.

    <Returns>
        True if this is the first close call to this socket, False otherwise.

    """
    self._closed = True
    return stopcomm(self._commid)




class TCPServerSocket (object):
  """
  This object is a wrapper around a listening
  TCP socket. It allows for accepting incoming
  connections, and closing the socket.

  It operates in a strictly non-blocking mode,
  and uses Exceptions to indicate when an
  operation would result in blocking behavior.
  """
  # Fields:
  # identity: This is a tuple which is our identity in the
  #           OPEN_SOCKET_INFO dictionary. We use this to
  #           perform the look-up for our info.
  #
  __slots__ = ["identity"]

  def __init__(self, identity):
    """
    <Purpose>
      Initializes the TCPServerSocket. The socket
      should already be established by listenforconnection
      prior to calling the initializer.

    <Arguments>
      identity: The identity tuple.

    <Exceptions>
      None

    <Returns>
      A TCPServerSocket
    """
    # Store our identity
    self.identity = identity


  def getconnection(self):
    """
    <Purpose>
      Accepts an incoming connection to a listening TCP socket.

    <Arguments>
      None

    <Exceptions>
      Raises SocketClosedLocal if close() has been called.
      Raises SocketWouldBlockError if the operation would block.

    <Resource Consumption>
      If successful, consumes 128 bytes of netrecv (64 bytes for
      a SYN and ACK packet) and 64 bytes of netsend (1 ACK packet).
      Uses an outsocket.

    <Returns>
      A tuple containing: (remote ip, remote port, socket object)
    """
    pass


  def close(self):
    """
    <Purpose>
      Closes the listening TCP socket.

    <Arguments>
      None

    <Exceptions>
      None

    <Side Effects>
      The IP and port can be re-used after closing.

    <Resource Consumption>
      Releases the insocket used.

    <Returns>
      True, if this is the first call to close.
      False otherwise.
    """
    pass


  def __del__(self):
    # Close the socket
    self.close()


