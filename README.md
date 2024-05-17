# Python barebones SMTP server

This is a barebones SMTP server implementation in Python that can receive emails (including supporting STARTTLS).

**NOTE:** This server ONLY RECEIVES EMAIL. It cannot send email.

I wrote this to learn the SMTP protocol and be able dynmaically route and process inbound emails.

## Example: Print details about incoming emails

The default handler class prints incoming messages by default:
```
import socketserver
from barebones_smtp_server import BarebonesSMTPHandler

with socketserver.ForkingTCPServer(("127.0.0.1", 9925), BarebonesSMTPHandler) as server:
    server.serve_forever()
```

Test using this python in another terminal window:
```
import smtplib
from email.message import EmailMessage
msg = EmailMessage()
msg.set_content("1" * 10000)
msg['Subject'] = f'subject1'
msg['From'] = "aaa@bbb.com"
msg['To'] = "someone@example.com"
s = smtplib.SMTP('127.0.0.1', port=9925)
s.ehlo()
s.send_message(msg)
s.quit()
```

## Example: Save incoming messages to your filesystem

`smtp_server.py` saves all incoming emails to the filesystem:
```
import re
import os
import time
import uuid
import socketserver
from barebones_smtp_server import BarebonesSMTPHandler

class MySMTPHandler(BarebonesSMTPHandler):
    ACCEPT_ADDRESSES = [re.compile("^[^@]+@example\.com$")]
    SERVER_DOMAIN = "mail.example.com"

    def received_inbound(self, peer, is_starttls, cmds, mail_from, rcpt_to, data):
        # ignore connections that didn't actually provide email data
        if data is not None:
            filename = "{}_{}.msg".format(int(time.time()), str(uuid.uuid4()))
            self.logger.info(f"Inbound email: {filename}")
            outfile = open(os.path.join("/tmp", filename), "wb")
            outfile.write(data)
            outfile.close()

if __name__ == "__main__":
    with socketserver.ForkingTCPServer(("127.0.0.1", 9925), MySMTPHandler) as server:
        server.serve_forever()
```

## Example: Run your SMTP server at startup (using cron)

`start.sh`:
```
#!/bin/bash
cd /path/to/barebones-smtp-server
nohup python3 -m smtp_server 2>> logfile.txt &
```

`stop.sh`:
```
#!/bin/bash
pkill --full "smtp_server"
```

Set in your cron (`crontab -e`):
```
@reboot /path/to/barebones-smtp-server/start.sh
```


## License

Released under the MIT license

