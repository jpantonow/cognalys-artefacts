# No action

The MD5 digest is calculated from the app's public package name and used to select configuration behavior. It is not used to protect secrets, verify data, or authenticate a connection, so MD5's cryptographic weaknesses do not create the reported security concern here.

Replacing the digest without updating the expected values would change which configuration is selected. No change or maintainer notification is warranted for this report.
