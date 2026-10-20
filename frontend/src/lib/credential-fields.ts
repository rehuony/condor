/**
 * Substrings that mark a connector config field as a sensitive credential.
 * Match both the field name and type so unfamiliar connector keys still use
 * password inputs. A broad match only masks a value while it is being edited.
 */
export const CREDENTIAL_FIELD_PATTERNS: readonly string[] = [
  "password",
  "passphrase",
  "secret",
  "token",
  "api_key",
  "apikey",
  "private_key",
  "privatekey",
  "mnemonic",
  "seed",
  "credential",
  "authorization",
  "key",
  "private",
];
