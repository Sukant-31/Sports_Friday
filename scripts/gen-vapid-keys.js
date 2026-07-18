// Prints a fresh VAPID key pair to paste into .env. Run once per environment:
//   pnpm gen:vapid
import webpush from 'web-push';

const { publicKey, privateKey } = webpush.generateVAPIDKeys();

console.log('# Add these to your .env');
console.log(`VAPID_PUBLIC_KEY=${publicKey}`);
console.log(`VAPID_PRIVATE_KEY=${privateKey}`);
