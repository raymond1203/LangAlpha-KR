import { describe, it, expect } from 'vitest';
import {
  getIndexSetForMarket,
  getNewsRegionForMarket,
  INDEX_SYMBOLS,
  KR_INDEX_SYMBOLS,
  KR_INDEX_NAMES,
} from '../api';

describe('getIndexSetForMarket', () => {
  it('returns US set for market=us', () => {
    const set = getIndexSetForMarket('us');
    expect(set.symbols).toEqual(INDEX_SYMBOLS);
    expect(set.names.GSPC).toBe('S&P 500');
  });

  it('returns KR set for market=kr', () => {
    const set = getIndexSetForMarket('kr');
    expect(set.symbols).toEqual(KR_INDEX_SYMBOLS);
    expect(set.names).toEqual(KR_INDEX_NAMES);
  });

  it('KR set covers KOSPI / KOSDAQ / KOSPI 200', () => {
    expect(KR_INDEX_SYMBOLS).toEqual(['KS11', 'KQ11', 'KS200']);
    expect(KR_INDEX_NAMES.KS11).toBe('코스피');
    expect(KR_INDEX_NAMES.KQ11).toBe('코스닥');
    expect(KR_INDEX_NAMES.KS200).toBe('코스피 200');
  });
});

describe('getNewsRegionForMarket', () => {
  it('returns "kr" for market=kr', () => {
    expect(getNewsRegionForMarket('kr')).toBe('kr');
  });

  it('returns undefined for market=us (글로벌 fallback)', () => {
    expect(getNewsRegionForMarket('us')).toBeUndefined();
  });
});
