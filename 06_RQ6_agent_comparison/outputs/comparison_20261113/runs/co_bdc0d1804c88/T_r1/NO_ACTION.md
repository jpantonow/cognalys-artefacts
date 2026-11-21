# No action

The MD5 digest is used to derive an internal configuration value from the app's package name. It is not used to protect sensitive data, authenticate anything, or verify integrity, so MD5's cryptographic weaknesses do not create the reported security problem here.

Replacing the digest would change the computed value and could change the app's behavior without providing a security benefit.
