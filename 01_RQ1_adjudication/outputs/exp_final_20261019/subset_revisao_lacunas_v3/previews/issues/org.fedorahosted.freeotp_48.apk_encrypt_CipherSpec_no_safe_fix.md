<!-- PREVIEW ONLY: this Issue has not been published to GitHub. -->

# Question about the Cipher use in encrypt

**Repository:** `freeotp/freeotp-android`  
**Type:** Issue

---

Hi! I'm a Computer Engineering student at the University of Brasília (UnB), working on a research project about how Android apps use security-sensitive APIs.

ares flagged `EncryptedKey.encrypt` in `mobile/src/main/java/org/fedorahosted/freeotp/encryptor/EncryptedKey.java`.

On the confirmed MasterKey caller path, a PBKDF2 SecretKeyFactory result is passed as EncryptedKey.encrypt's key argument and then directly to AES/GCM Cipher.init. CipherSpec requires that initialization key to carry generatedKey for the transformation algorithm, generatedPubkey, or generatedPrivkey; this PBKDF2-derived value carries none of those admitted origins.

I have not opened a pull request because I could not find a change that keeps the current behaviour intact and still builds cleanly with the rest of the code. Is there a constraint on this code I may be missing, and would you be open to a change here?

With your answer I can prepare a change and send it for review.

Thanks!
