// Bitcoin utilities for Serendipity Web UI
// Uses noble libraries for cryptographic operations (self-hosted)

import * as secp256k1 from '/static/js/vendor/secp256k1.js';
import { sha256 } from '/static/js/vendor/sha256.js';
import { ripemd160 } from '/static/js/vendor/ripemd160.js';

// Configure secp256k1 to use pure JS HMAC-SHA256 for non-secure contexts (HTTP on non-localhost)
function hmacSha256(key, ...messages) {
    const blockSize = 64;
    
    // Ensure key is Uint8Array
    if (!(key instanceof Uint8Array)) {
        key = new Uint8Array(key);
    }
    
    // If key is longer than block size, hash it
    if (key.length > blockSize) {
        key = sha256(key);
    }
    
    // Pad key to block size
    const paddedKey = new Uint8Array(blockSize);
    paddedKey.set(key);
    
    // Create inner and outer padding
    const ipad = new Uint8Array(blockSize);
    const opad = new Uint8Array(blockSize);
    for (let i = 0; i < blockSize; i++) {
        ipad[i] = paddedKey[i] ^ 0x36;
        opad[i] = paddedKey[i] ^ 0x5c;
    }
    
    // Concatenate all messages
    let totalLen = 0;
    for (const msg of messages) {
        totalLen += msg.length;
    }
    const allMessages = new Uint8Array(totalLen);
    let offset = 0;
    for (const msg of messages) {
        allMessages.set(msg, offset);
        offset += msg.length;
    }
    
    // Inner hash: H(ipad || messages)
    const innerData = new Uint8Array(blockSize + allMessages.length);
    innerData.set(ipad);
    innerData.set(allMessages, blockSize);
    const innerHash = sha256(innerData);
    
    // Outer hash: H(opad || innerHash)
    const outerData = new Uint8Array(blockSize + innerHash.length);
    outerData.set(opad);
    outerData.set(innerHash, blockSize);
    
    return sha256(outerData);
}

// Set the HMAC function for secp256k1 (works in non-secure contexts)
if (secp256k1.etc) {
    secp256k1.etc.hmacSha256Sync = hmacSha256;
}

const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

export function base58Decode(str) {
    const bytes = [];
    for (let i = 0; i < str.length; i++) {
        const char = str[i];
        const index = BASE58_ALPHABET.indexOf(char);
        if (index === -1) throw new Error('Invalid base58 character');
        
        let carry = index;
        for (let j = 0; j < bytes.length; j++) {
            carry += bytes[j] * 58;
            bytes[j] = carry & 0xff;
            carry >>= 8;
        }
        while (carry > 0) {
            bytes.push(carry & 0xff);
            carry >>= 8;
        }
    }
    for (let i = 0; i < str.length && str[i] === '1'; i++) {
        bytes.push(0);
    }
    return new Uint8Array(bytes.reverse());
}

export function base58Encode(bytes) {
    const digits = [0];
    for (let i = 0; i < bytes.length; i++) {
        let carry = bytes[i];
        for (let j = 0; j < digits.length; j++) {
            carry += digits[j] << 8;
            digits[j] = carry % 58;
            carry = (carry / 58) | 0;
        }
        while (carry > 0) {
            digits.push(carry % 58);
            carry = (carry / 58) | 0;
        }
    }
    let result = '';
    for (let i = 0; i < bytes.length && bytes[i] === 0; i++) {
        result += '1';
    }
    for (let i = digits.length - 1; i >= 0; i--) {
        result += BASE58_ALPHABET[digits[i]];
    }
    return result;
}

export function wifToPrivateKey(wif) {
    const decoded = base58Decode(wif);
    // A valid WIF decodes to exactly 37 bytes (uncompressed: 1 version byte +
    // 32 key bytes + 4 checksum bytes) or 38 bytes (compressed: an extra
    // trailing 0x01 flag byte). Anything else is malformed.
    if (decoded.length !== 37 && decoded.length !== 38) {
        throw new Error('Invalid WIF: bad length');
    }
    // Verify the 4-byte Base58Check checksum (last 4 bytes of the payload).
    const payload = decoded.slice(0, decoded.length - 4);
    const checksum = decoded.slice(decoded.length - 4);
    const expected = sha256(sha256(payload)).slice(0, 4);
    for (let i = 0; i < 4; i++) {
        if (checksum[i] !== expected[i]) {
            throw new Error('Invalid WIF: bad checksum');
        }
    }
    // WIF for mainnet uses version byte 0x80.
    if (payload[0] !== 0x80) {
        throw new Error('Invalid WIF: unsupported version byte');
    }
    // A compressed WIF carries a trailing 0x01 flag byte after the 32 key bytes.
    if (payload.length === 34 && payload[33] !== 0x01) {
        throw new Error('Invalid WIF: bad compression flag');
    }
    // The 32-byte private key follows the version byte.
    return payload.slice(1, 33);
}

export function getPublicKey(privateKey, compressed = true) {
    return secp256k1.getPublicKey(privateKey, compressed);
}

export function publicKeyToAddress(publicKey) {
    const sha = sha256(publicKey);
    const hash160 = ripemd160(sha);
    
    const versioned = new Uint8Array(21);
    versioned[0] = 0x00;
    versioned.set(hash160, 1);
    
    const checksum = sha256(sha256(versioned)).slice(0, 4);
    
    const addressBytes = new Uint8Array(25);
    addressBytes.set(versioned);
    addressBytes.set(checksum, 21);
    
    return base58Encode(addressBytes);
}

export function wifToAddress(wif) {
    try {
        const privateKey = wifToPrivateKey(wif);
        const publicKey = getPublicKey(privateKey, true);
        return publicKeyToAddress(publicKey);
    } catch (e) {
        console.error('Invalid WIF:', e.message);
        return null;
    }
}

function encodeVarInt(n) {
    if (n < 253) {
        return new Uint8Array([n]);
    } else if (n < 0x10000) {
        return new Uint8Array([253, n & 0xff, (n >> 8) & 0xff]);
    } else if (n < 0x100000000) {
        return new Uint8Array([254, n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >> 24) & 0xff]);
    } else {
        throw new Error('Message too long');
    }
}

export async function signMessage(message, wif) {
    const privateKey = wifToPrivateKey(wif);
    
    const prefix = 'Bitcoin Signed Message:\n';
    const prefixBytes = new TextEncoder().encode(prefix);
    const messageBytes = new TextEncoder().encode(message);
    
    const prefixLen = encodeVarInt(prefixBytes.length);
    const messageLen = encodeVarInt(messageBytes.length);
    
    const fullMessage = new Uint8Array(prefixLen.length + prefixBytes.length + messageLen.length + messageBytes.length);
    let offset = 0;
    fullMessage.set(prefixLen, offset); offset += prefixLen.length;
    fullMessage.set(prefixBytes, offset); offset += prefixBytes.length;
    fullMessage.set(messageLen, offset); offset += messageLen.length;
    fullMessage.set(messageBytes, offset);
    
    const messageHash = sha256(sha256(fullMessage));
    
    // Use sync version since we configured hmacSha256Sync for non-secure contexts
    const signature = secp256k1.sign(messageHash, privateKey, { lowS: true });
    const recovery = signature.recovery;
    
    const header = 27 + recovery + 4;
    const sigBytes = new Uint8Array(65);
    sigBytes[0] = header;
    sigBytes.set(signature.toCompactRawBytes(), 1);
    
    return btoa(String.fromCharCode(...sigBytes));
}

function jsonSortedEncode(obj, indent = '  ') {
    function sortObject(o) {
        if (o === null || typeof o !== 'object') {
            return o;
        }
        if (Array.isArray(o)) {
            return o.map(sortObject);
        }
        const sorted = {};
        Object.keys(o).sort().forEach(key => {
            sorted[key] = sortObject(o[key]);
        });
        return sorted;
    }
    return JSON.stringify(sortObject(obj), null, indent);
}

export async function signData(messageData, wif) {
    const jsonWithIndent = jsonSortedEncode(messageData, '  ');
    
    // Use the pure JS sha256 implementation instead of crypto.subtle
    // This works in non-secure contexts (HTTP on non-localhost)
    const encoder = new TextEncoder();
    const hashBytes = sha256(encoder.encode(jsonWithIndent));
    const sha256Hash = Array.from(hashBytes).map(b => b.toString(16).padStart(2, '0')).join('');
    
    const message = `/sha256/${sha256Hash}`;
    const signature = await signMessage(message, wif);
    const address = wifToAddress(wif);
    
    return {
        message: message,
        signature: signature,
        address: address,
        data: messageData
    };
}

export function generateWifKey() {
    const privateKey = window.crypto.getRandomValues(new Uint8Array(32));
    
    const extended = new Uint8Array(34);
    extended[0] = 0x80;
    extended.set(privateKey, 1);
    extended[33] = 0x01;
    
    const checksum = sha256(sha256(extended)).slice(0, 4);
    
    const full = new Uint8Array(38);
    full.set(extended);
    full.set(checksum, 34);
    
    return base58Encode(full);
}

// Storage utilities
const STORAGE_KEY = 'serendipity_wif';

// WIF-at-rest encryption.
//
// The Web Crypto `crypto.subtle` (SubtleCrypto) API is only exposed in secure
// contexts (HTTPS, or http://localhost / http://127.0.0.1). When the UI is served
// over plain HTTP on a LAN address, `crypto.subtle` is `undefined`, so a
// PBKDF2 + AES-GCM implementation throws
// "Cannot read properties of undefined (reading 'importKey')" and the key can
// neither be saved nor unlocked. To work in every context the encryption below is
// implemented in pure JavaScript on top of the vendored SHA-256:
//
//   * key derivation: PBKDF2-HMAC-SHA256 (64-byte output)
//   * encryption:     ChaCha20 (RFC 8439) stream cipher
//   * authentication: HMAC-SHA256 over magic || iterations || salt || nonce || ciphertext
//                     (encrypt-then-MAC)
//
// The current stored blob is
// base64(magic "SCv3" || iterations(u32 big-endian) || salt(16) || nonce(12) || ciphertext || tag(32)).
// The PBKDF2 iteration count is self-describing: it is written into the header so
// that raising the default iteration count in the future never breaks blobs that
// were written with a lower count. Older pure-JS blobs ("SCv2", fixed 1000
// iterations) and legacy AES-GCM blobs (no magic prefix) are still decryptable.
const WIF_CRYPTO_MAGIC = new Uint8Array([0x53, 0x43, 0x76, 0x33]); // "SCv3"
const WIF_CRYPTO_MAGIC_V2 = new Uint8Array([0x53, 0x43, 0x76, 0x32]); // "SCv2"
const WIF_PBKDF2_ITERATIONS = 600000;
const WIF_LEGACY_PBKDF2_ITERATIONS = 1000;
const WIF_LEGACY_AES_GCM_ITERATIONS = 1000;
const WIF_SALT_LENGTH = 16;
const WIF_NONCE_LENGTH = 12;
const WIF_TAG_LENGTH = 32;
const WIF_ITERATIONS_LENGTH = 4;

function utf8Encode(value) {
    return new TextEncoder().encode(value);
}

function utf8Decode(bytes) {
    return new TextDecoder().decode(bytes);
}

function concatBytes(...arrays) {
    let total = 0;
    for (const array of arrays) total += array.length;
    const combined = new Uint8Array(total);
    let offset = 0;
    for (const array of arrays) {
        combined.set(array, offset);
        offset += array.length;
    }
    return combined;
}

function constantTimeEqual(a, b) {
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
    return diff === 0;
}

function writeUint32BE(value) {
    return new Uint8Array([
        (value >>> 24) & 0xff,
        (value >>> 16) & 0xff,
        (value >>> 8) & 0xff,
        value & 0xff
    ]);
}

function readUint32BE(bytes, offset) {
    return ((bytes[offset] << 24) | (bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3]) >>> 0;
}

// PBKDF2-HMAC-SHA256 (RFC 8018) built on the existing `hmacSha256` helper.
function pbkdf2Sha256(password, salt, iterations, keyLength) {
    const hashLength = 32;
    const blockCount = Math.ceil(keyLength / hashLength);
    const derived = new Uint8Array(blockCount * hashLength);
    const saltBlock = new Uint8Array(salt.length + 4);
    saltBlock.set(salt, 0);
    for (let block = 1; block <= blockCount; block++) {
        saltBlock[salt.length] = (block >>> 24) & 0xff;
        saltBlock[salt.length + 1] = (block >>> 16) & 0xff;
        saltBlock[salt.length + 2] = (block >>> 8) & 0xff;
        saltBlock[salt.length + 3] = block & 0xff;
        let u = hmacSha256(password, saltBlock);
        const accumulator = u.slice();
        for (let iteration = 1; iteration < iterations; iteration++) {
            u = hmacSha256(password, u);
            for (let i = 0; i < hashLength; i++) accumulator[i] ^= u[i];
        }
        derived.set(accumulator, (block - 1) * hashLength);
    }
    return derived.slice(0, keyLength);
}

function rotateLeft32(value, shift) {
    return ((value << shift) | (value >>> (32 - shift))) >>> 0;
}

function chacha20QuarterRound(state, a, b, c, d) {
    state[a] = (state[a] + state[b]) >>> 0; state[d] = rotateLeft32(state[d] ^ state[a], 16);
    state[c] = (state[c] + state[d]) >>> 0; state[b] = rotateLeft32(state[b] ^ state[c], 12);
    state[a] = (state[a] + state[b]) >>> 0; state[d] = rotateLeft32(state[d] ^ state[a], 8);
    state[c] = (state[c] + state[d]) >>> 0; state[b] = rotateLeft32(state[b] ^ state[c], 7);
}

function readLittleEndian32(bytes, offset) {
    return (bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16) | (bytes[offset + 3] << 24)) >>> 0;
}

// One 64-byte ChaCha20 keystream block (RFC 8439, 96-bit nonce, 32-bit counter).
function chacha20Block(key, counter, nonce) {
    const state = new Uint32Array(16);
    state[0] = 0x61707865;
    state[1] = 0x3320646e;
    state[2] = 0x79622d32;
    state[3] = 0x6b206574;
    for (let i = 0; i < 8; i++) state[4 + i] = readLittleEndian32(key, i * 4);
    state[12] = counter >>> 0;
    for (let i = 0; i < 3; i++) state[13 + i] = readLittleEndian32(nonce, i * 4);

    const working = state.slice();
    for (let round = 0; round < 10; round++) {
        chacha20QuarterRound(working, 0, 4, 8, 12);
        chacha20QuarterRound(working, 1, 5, 9, 13);
        chacha20QuarterRound(working, 2, 6, 10, 14);
        chacha20QuarterRound(working, 3, 7, 11, 15);
        chacha20QuarterRound(working, 0, 5, 10, 15);
        chacha20QuarterRound(working, 1, 6, 11, 12);
        chacha20QuarterRound(working, 2, 7, 8, 13);
        chacha20QuarterRound(working, 3, 4, 9, 14);
    }

    const block = new Uint8Array(64);
    for (let i = 0; i < 16; i++) {
        const word = (working[i] + state[i]) >>> 0;
        block[i * 4] = word & 0xff;
        block[i * 4 + 1] = (word >>> 8) & 0xff;
        block[i * 4 + 2] = (word >>> 16) & 0xff;
        block[i * 4 + 3] = (word >>> 24) & 0xff;
    }
    return block;
}

function chacha20Xor(key, nonce, counter, data) {
    const output = new Uint8Array(data.length);
    let offset = 0;
    let blockCounter = counter >>> 0;
    while (offset < data.length) {
        const block = chacha20Block(key, blockCounter, nonce);
        const chunkLength = Math.min(64, data.length - offset);
        for (let i = 0; i < chunkLength; i++) output[offset + i] = data[offset + i] ^ block[i];
        offset += chunkLength;
        blockCounter = (blockCounter + 1) >>> 0;
    }
    return output;
}

function deriveWifKeys(password, salt, iterations) {
    const keyMaterial = pbkdf2Sha256(utf8Encode(password), salt, iterations, 64);
    return { encryptionKey: keyMaterial.slice(0, 32), macKey: keyMaterial.slice(32, 64) };
}

export async function encryptWif(wif, password, iterations = WIF_PBKDF2_ITERATIONS) {
    const salt = window.crypto.getRandomValues(new Uint8Array(WIF_SALT_LENGTH));
    const nonce = window.crypto.getRandomValues(new Uint8Array(WIF_NONCE_LENGTH));
    const { encryptionKey, macKey } = deriveWifKeys(password, salt, iterations);

    const ciphertext = chacha20Xor(encryptionKey, nonce, 0, utf8Encode(wif));
    const iterationsBytes = writeUint32BE(iterations);
    const header = concatBytes(WIF_CRYPTO_MAGIC, iterationsBytes, salt, nonce);
    const tag = hmacSha256(macKey, header, ciphertext);

    return btoa(String.fromCharCode(...concatBytes(header, ciphertext, tag)));
}

// Decrypt a blob written by the legacy `crypto.subtle` AES-GCM implementation.
// Only possible in a secure context (where `crypto.subtle` exists).
async function decryptLegacyWif(encryptedBase64, password) {
    if (!window.crypto || !window.crypto.subtle) {
        throw new Error(
            'This key was stored with the legacy AES-GCM format, which requires a secure context (HTTPS or localhost). Please re-enter your WIF key.'
        );
    }
    const combined = Uint8Array.from(atob(encryptedBase64), c => c.charCodeAt(0));
    const salt = combined.slice(0, 16);
    const iv = combined.slice(16, 28);
    const encrypted = combined.slice(28);
    const keyMaterial = await window.crypto.subtle.importKey('raw', utf8Encode(password), 'PBKDF2', false, ['deriveKey']);
    const key = await window.crypto.subtle.deriveKey(
        { name: 'PBKDF2', salt: salt, iterations: WIF_LEGACY_AES_GCM_ITERATIONS, hash: 'SHA-256' },
        keyMaterial,
        { name: 'AES-GCM', length: 256 },
        false,
        ['decrypt']
    );
    const decrypted = await window.crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv }, key, encrypted);
    return utf8Decode(decrypted);
}

export async function decryptWif(encryptedBase64, password) {
    const combined = Uint8Array.from(atob(encryptedBase64), c => c.charCodeAt(0));

    const isScv3 = combined.length >= WIF_CRYPTO_MAGIC.length &&
        constantTimeEqual(combined.slice(0, WIF_CRYPTO_MAGIC.length), WIF_CRYPTO_MAGIC);
    const isScv2 = combined.length >= WIF_CRYPTO_MAGIC_V2.length &&
        constantTimeEqual(combined.slice(0, WIF_CRYPTO_MAGIC_V2.length), WIF_CRYPTO_MAGIC_V2);

    if (isScv3) {
        const minimumLength = WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH + WIF_SALT_LENGTH + WIF_NONCE_LENGTH + WIF_TAG_LENGTH;
        if (combined.length < minimumLength) {
            throw new Error('Invalid password or corrupted WIF data');
        }

        const iterationsBytes = combined.slice(WIF_CRYPTO_MAGIC.length, WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH);
        const iterations = readUint32BE(iterationsBytes, 0);
        const salt = combined.slice(WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH, WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH + WIF_SALT_LENGTH);
        const nonce = combined.slice(WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH + WIF_SALT_LENGTH, WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH + WIF_SALT_LENGTH + WIF_NONCE_LENGTH);
        const ciphertext = combined.slice(WIF_CRYPTO_MAGIC.length + WIF_ITERATIONS_LENGTH + WIF_SALT_LENGTH + WIF_NONCE_LENGTH, combined.length - WIF_TAG_LENGTH);
        const tag = combined.slice(combined.length - WIF_TAG_LENGTH);

        const { encryptionKey, macKey } = deriveWifKeys(password, salt, iterations);
        const header = concatBytes(WIF_CRYPTO_MAGIC, iterationsBytes, salt, nonce);
        const expectedTag = hmacSha256(macKey, header, ciphertext);
        if (!constantTimeEqual(tag, expectedTag)) {
            throw new Error('Invalid password or corrupted WIF data');
        }

        return utf8Decode(chacha20Xor(encryptionKey, nonce, 0, ciphertext));
    }

    if (isScv2) {
        const minimumLength = WIF_CRYPTO_MAGIC_V2.length + WIF_SALT_LENGTH + WIF_NONCE_LENGTH + WIF_TAG_LENGTH;
        if (combined.length < minimumLength) {
            throw new Error('Invalid password or corrupted WIF data');
        }

        const salt = combined.slice(WIF_CRYPTO_MAGIC_V2.length, WIF_CRYPTO_MAGIC_V2.length + WIF_SALT_LENGTH);
        const nonce = combined.slice(WIF_CRYPTO_MAGIC_V2.length + WIF_SALT_LENGTH, WIF_CRYPTO_MAGIC_V2.length + WIF_SALT_LENGTH + WIF_NONCE_LENGTH);
        const ciphertext = combined.slice(WIF_CRYPTO_MAGIC_V2.length + WIF_SALT_LENGTH + WIF_NONCE_LENGTH, combined.length - WIF_TAG_LENGTH);
        const tag = combined.slice(combined.length - WIF_TAG_LENGTH);

        const { encryptionKey, macKey } = deriveWifKeys(password, salt, WIF_LEGACY_PBKDF2_ITERATIONS);
        const header = concatBytes(WIF_CRYPTO_MAGIC_V2, salt, nonce);
        const expectedTag = hmacSha256(macKey, header, ciphertext);
        if (!constantTimeEqual(tag, expectedTag)) {
            throw new Error('Invalid password or corrupted WIF data');
        }

        return utf8Decode(chacha20Xor(encryptionKey, nonce, 0, ciphertext));
    }

    return decryptLegacyWif(encryptedBase64, password);
}

export function saveWif(wif, encrypted, encryptedData = null) {
    if (!encrypted || !encryptedData) {
        throw new Error('Refusing to store an unencrypted WIF');
    }
    const storageData = {
        encrypted: true,
        data: encryptedData,
        timestamp: new Date().toISOString()
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(storageData));
}

export function loadWifStorage() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return null;
    return JSON.parse(stored);
}

export function clearWifStorage() {
    localStorage.removeItem(STORAGE_KEY);
}

export function isLegacyPlaintextWif(storage) {
    // Legacy shape stored the raw WIF in `data` with `encrypted` falsy/absent.
    // New shape always has encrypted === true and an encrypted base64 blob in data.
    return Boolean(storage && storage.data && storage.encrypted !== true);
}

// Session storage for the decrypted WIF key.
//
// The encrypted WIF lives in localStorage (persists across sessions, at rest).
// The decrypted WIF lives in sessionStorage, which is per-origin and per-tab,
// cleared automatically when the tab/window closes, and not shared with other
// tabs. This is an intentional middle ground: it avoids re-prompting for the
// password on every page navigation while never writing the decrypted key to
// disk or leaking it across tabs.
const SESSION_STORAGE_KEY = 'wif_session_key';

export function saveSessionWif(wif) {
    sessionStorage.setItem(SESSION_STORAGE_KEY, wif);
}

export function loadSessionWif() {
    return sessionStorage.getItem(SESSION_STORAGE_KEY);
}

export function clearSessionWif() {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
}
