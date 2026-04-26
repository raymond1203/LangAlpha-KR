import { describe, it, expect } from 'vitest';
import {
  getIndexSetForLocale,
  INDEX_SYMBOLS,
  KR_INDEX_SYMBOLS,
  KR_INDEX_NAMES,
} from '../api';

describe('getIndexSetForLocale', () => {
  it('returns US set for en-US', () => {
    const set = getIndexSetForLocale('en-US');
    expect(set.symbols).toEqual(INDEX_SYMBOLS);
    expect(set.names.GSPC).toBe('S&P 500');
  });

  it('returns US set for zh-CN (non-ko locale)', () => {
    const set = getIndexSetForLocale('zh-CN');
    expect(set.symbols).toEqual(INDEX_SYMBOLS);
  });

  it('returns KR set for ko-KR', () => {
    const set = getIndexSetForLocale('ko-KR');
    expect(set.symbols).toEqual(KR_INDEX_SYMBOLS);
    expect(set.names).toEqual(KR_INDEX_NAMES);
  });

  it('returns KR set for bare "ko"', () => {
    const set = getIndexSetForLocale('ko');
    expect(set.symbols).toEqual(KR_INDEX_SYMBOLS);
  });

  it('is case-insensitive on locale prefix', () => {
    expect(getIndexSetForLocale('KO-KR').symbols).toEqual(KR_INDEX_SYMBOLS);
  });

  it('falls back to US set for undefined / null / empty', () => {
    expect(getIndexSetForLocale(undefined).symbols).toEqual(INDEX_SYMBOLS);
    expect(getIndexSetForLocale(null).symbols).toEqual(INDEX_SYMBOLS);
    expect(getIndexSetForLocale('').symbols).toEqual(INDEX_SYMBOLS);
  });

  it('KR set covers KOSPI / KOSDAQ / KOSPI 200', () => {
    expect(KR_INDEX_SYMBOLS).toEqual(['KS11', 'KQ11', 'KS200']);
    expect(KR_INDEX_NAMES.KS11).toBe('코스피');
    expect(KR_INDEX_NAMES.KQ11).toBe('코스닥');
    expect(KR_INDEX_NAMES.KS200).toBe('코스피 200');
  });
});
