import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { config, usersRepo } from '@sports/shared';
import { httpError } from '../middleware/errorHandler.js';

const BCRYPT_COST = 12;
const TOKEN_TTL = '7d';

export async function signup({ email, password }) {
  const existing = await usersRepo.findUserByEmail(email);
  if (existing) throw httpError(409, 'Email already registered');
  const passwordHash = await bcrypt.hash(password, BCRYPT_COST);
  const user = await usersRepo.createUser({ email, passwordHash });
  return { id: user.id, email: user.email };
}

export async function login({ email, password }) {
  const user = await usersRepo.findUserByEmail(email);
  if (!user) throw httpError(401, 'Invalid credentials');
  const ok = await bcrypt.compare(password, user.password_hash);
  if (!ok) throw httpError(401, 'Invalid credentials');
  return { id: user.id, email: user.email };
}

export function signToken(userId) {
  return jwt.sign({ sub: userId }, config.jwtSecret, { expiresIn: TOKEN_TTL });
}

export function authCookieOptions() {
  return {
    httpOnly: true,
    secure: config.env === 'production',
    sameSite: 'lax',
    maxAge: 7 * 24 * 60 * 60 * 1000,
    path: '/',
  };
}
