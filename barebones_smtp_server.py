import re
import ssl
import time
import logging
import email.utils
import socketserver

# default logging settings
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.StreamHandler())
LOGGER.setLevel(logging.INFO)

class BarebonesSMTPHandler(socketserver.BaseRequestHandler):
    """
    A very primitive SMTP server that can handle STARTTLS
    and passes details and messages to subclassed methods.
    """
    # server config
    ACCEPT_ADDRESSES = [re.compile("^[^@]+@example\.com$")]  # override by subclass
    SERVER_DOMAIN = "mail.example.com"  # override in subclass
    SERVER_TLS_CERT = None  # override in subclass (e.g. "/path/to/ssl.crt")
    SERVER_TLS_KEY = None   # override in subclass (e.g. "/path/to/ssl.key")
    CHUNK_SIZE = 1024
    CMD_SIZE = 1024
    DATA_MAX_SIZE = 100 * 1000 * 1000 # 100MB
    logger = LOGGER

    def handle(self):
        """
        Handle command back-and-forth of an client connecting to this SMTP
        server and then passing the resulting details and received message
        to the subclassed .received_inbound(...) method.
        """
        # defaults
        expecting_data = False
        seen_helo = False
        seen_quit = False
        is_starttls = False
        cmds = []       # [("PEER|SERVER", <timestamp>, "<command>"), ...]
        mail_from = []  # ['"Alice" <alice-from@example.com>', ...]
        rcpt_to = []    # ['"Bob" <bob-to@example.com>', ...]
        peer = list(self.request.getpeername()) # ["<ip_address>", <port>]
        data = None     # b"..."

        # helper function for logging commands before sending them
        def _send_response(resp, cmd_log):
            self.logger.debug(f"CMD RESPONSE: {resp}")
            cmd_log.append(("SERVER", time.time(), resp))
            self.request.sendall(resp)

        # send greeting
        _send_response(b"220 " + self.SERVER_DOMAIN.encode() + b"\r\n", cmds)

        # start listening for commands and responding to them
        while True:

            # receive mail payload
            if expecting_data:
                data = b""
                while True:
                    chunk = self.request.recv(self.CHUNK_SIZE)
                    self.logger.debug(f"DATA RECEIVED: {len(chunk)} bytes")
                    #print("#######chunk = {}".format(chunk))
                    data += chunk
                    if b"\r\n.\r\n" in data[(-1 * (self.CHUNK_SIZE + 6)):]:
                        data = data.split(b"\r\n.\r\n")[0]
                        #print("#######chunk_break")
                        break
                    if len(data) >= self.DATA_MAX_SIZE:
                        break
                expecting_data = False
                if len(data) >= self.DATA_MAX_SIZE:
                    _send_response(b"554 5.3.4 Message too big for system\r\n", cmds)
                else:
                    _send_response(b"250 2.6.0 Message accepted\r\n", cmds)

            # handle smtp commands
            else:
                expecting_data = False
                cmd = self.request.recv(self.CMD_SIZE)
                self.logger.debug(f"CMD RECEIVED: {cmd}")
                cmds.append(("PEER", time.time(), cmd))
                cmdupper = cmd.upper()
                # command needs to be less than the buffer size
                if len(cmd) == self.CMD_SIZE:
                    _send_response(b"500 Error: command too long\r\n", cmds)

                # HELO/EHLO
                elif cmdupper.startswith(b"EHLO ") or cmdupper.startswith(b"HELO "):
                    mail_from = []
                    rcpt_to = []
                    seen_helo = True
                    # indicate that the server supports TLS if a cert is set
                    if self.SERVER_TLS_CERT:
                        _send_response(b"250-" + self.SERVER_DOMAIN.encode() + b"\r\n", cmds)
                        _send_response(b"250 STARTTLS\r\n", cmds)
                    else:
                        _send_response(b"250 " + self.SERVER_DOMAIN.encode() + b"\r\n", cmds)

                # anything else requires a HELO first
                elif not seen_helo:
                    _send_response(b"503 Error: send HELO first\r\n", cmds)

                # STARTTLS
                elif cmdupper == b"STARTTLS\r\n":
                    # reset connection setup
                    mail_from = []
                    rcpt_to = []
                    seen_helo = False
                    # establish secure connection
                    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                    ssl_ctx.load_cert_chain(certfile=self.SERVER_TLS_CERT, keyfile=self.SERVER_TLS_KEY)
                    _send_response(b"220 2.0.0 Ready to start TLS\r\n", cmds)
                    self.request = ssl_ctx.wrap_socket(self.request, server_side=True)
                    peer = list(self.request.getpeername())
                    is_starttls = isinstance(self.request, ssl.SSLSocket)

                # MAIL FROM
                elif cmdupper.startswith(b"MAIL FROM:"):
                    mail_from.append(cmd.split(b":", 1)[1].strip().decode())
                    _send_response(b"250 Ok\r\n", cmds)

                # RCPT TO
                elif cmdupper.startswith(b"RCPT TO:"):
                    # reject if receipient is not on one of the listening domains
                    rcpt_to_raw = cmd.split(b":", 1)[1].strip().decode()
                    rcpt_to_addr = email.utils.parseaddr(rcpt_to_raw)[1]
                    if not any(l.match(rcpt_to_addr) for l in self.ACCEPT_ADDRESSES):
                        _send_response(b"550 Error: recipient not found\r\n", cmds)
                        self.request.close()
                        break
                    # recipient is valid, so add them to the list
                    rcpt_to.append(rcpt_to_raw)
                    _send_response(b"250 Ok\r\n", cmds)

                # DATA
                elif cmdupper == b"DATA\r\n":
                    # ready to start getting data only if we know who sent and is receiving
                    if mail_from and rcpt_to:
                        expecting_data = True
                        _send_response(b"354 End data with <CR><LF>.<CR><LF>\r\n", cmds)
                    # reject data if not yet provided recipient or from address
                    else:
                        _send_response(b"503 Error: need 'MAIL FROM' and 'RCPT TO' before sending data\r\n", cmds)
                        self.request.close()
                        break

                # QUIT
                elif cmdupper == b"QUIT\r\n":
                    seen_quit = True
                    # say goodbye and close the connection
                    try:
                        _send_response(b"221 2.0.0 Goodbye\r\n", cmds)
                        self.request.close()
                    # sometimes clients have already closed the connection
                    except ssl.SSLEOFError:
                        pass
                    break

                # Unknown command
                else:
                    _send_response(b"500 Error: unsupported command\r\n", cmds)
                    self.request.close()
                    break

        # pass off the message to the subclassed handler
        self.received_inbound(peer, is_starttls, cmds, mail_from, rcpt_to, data)

    def received_inbound(self, peer, is_starttls, cmds, mail_from, rcpt_to, data):
        """
        Print the received args by default.
        You should override this method in a subclass to actually handle received emails.
        """
        self.logger.info("==START Inbound==")
        self.logger.info(f"peer = {peer}")
        self.logger.info(f"is_starttls = {is_starttls}")
        self.logger.info(f"cmds = {cmds}")
        self.logger.info(f"mail_from = {mail_from}")
        self.logger.info(f"rcpt_to = {rcpt_to}")
        if (data is not None) and len(data) > 100:
            self.logger.info(f"data ({len(data)} bytes) = {data[:100]} (truncated to 100 bytes)")
        elif data is not None:
            self.logger.info(f"data ({len(data)} bytes) = {data}")
        else:
            self.logger.info("data = None")
        self.logger.info("==END Inbound==")


# If called directly, run an example server that just prints the received messages
if __name__ == "__main__":
    import argparse, importlib
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default="127.0.0.1")
    parser.add_argument('--port', default=9925, type=int)
    parser.add_argument("--debug", action="store_const", const=logging.DEBUG)
    args = parser.parse_args()

    LOGGER.setLevel(args.debug or LOGGER.level)
    LOGGER.info("Running SMTP server on {}:{}...".format(args.host, args.port))

    socketserver.ForkingTCPServer.allow_reuse_address = True
    with socketserver.ForkingTCPServer((args.host, args.port), BarebonesSMTPHandler) as server:
        server.serve_forever()

"""
#Example
import smtplib
from email.message import EmailMessage
msg = EmailMessage()
msg.set_content("1" * 10000)
msg['Subject'] = f'subject1'
msg['From'] = "aaa@bbb.com"
msg['To'] = "someone@example.com"
s = smtplib.SMTP('127.0.0.1', port=9925)
s.ehlo()
#s.starttls() # uncomment if TLS cert is set
s.send_message(msg)
s.quit()
"""

