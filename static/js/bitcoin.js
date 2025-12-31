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
    const privateKey = decoded.slice(1, decoded[0] === 0x80 ? (decoded.length > 37 ? 33 : 33) : 33);
    if (privateKey.length === 33) {
        return privateKey.slice(0, 32);
    }
    return privateKey.slice(0, 32);
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
        console.error('Error deriving address:', e);
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

export async function deriveEncryptionKey(password, salt) {
    const encoder = new TextEncoder();
    const keyMaterial = await window.crypto.subtle.importKey(
        'raw',
        encoder.encode(password),
        'PBKDF2',
        false,
        ['deriveKey']
    );
    
    return window.crypto.subtle.deriveKey(
        {
            name: 'PBKDF2',
            salt: salt,
            iterations: 100000,
            hash: 'SHA-256'
        },
        keyMaterial,
        { name: 'AES-GCM', length: 256 },
        false,
        ['encrypt', 'decrypt']
    );
}

export async function encryptWif(wif, password) {
    const encoder = new TextEncoder();
    const salt = window.crypto.getRandomValues(new Uint8Array(16));
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveEncryptionKey(password, salt);
    
    const encrypted = await window.crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: iv },
        key,
        encoder.encode(wif)
    );
    
    const combined = new Uint8Array(salt.length + iv.length + encrypted.byteLength);
    combined.set(salt, 0);
    combined.set(iv, salt.length);
    combined.set(new Uint8Array(encrypted), salt.length + iv.length);
    
    return btoa(String.fromCharCode(...combined));
}

export async function decryptWif(encryptedBase64, password) {
    const combined = Uint8Array.from(atob(encryptedBase64), c => c.charCodeAt(0));
    
    const salt = combined.slice(0, 16);
    const iv = combined.slice(16, 28);
    const encrypted = combined.slice(28);
    
    const key = await deriveEncryptionKey(password, salt);
    
    const decrypted = await window.crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: iv },
        key,
        encrypted
    );
    
    return new TextDecoder().decode(decrypted);
}

export function saveWif(wif, encrypted, encryptedData = null) {
    const storageData = {
        encrypted: encrypted,
        data: encryptedData || wif,
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
