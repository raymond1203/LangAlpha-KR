import React, { useState, useEffect } from 'react';
import { ArrowLeft, Search } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../../components/ui/dialog';
import { Input } from '../../../components/ui/input';
import { ScrollArea } from '../../../components/ui/scroll-area';
import { searchStocks } from '@/lib/marketUtils';

interface StockResult {
  symbol: string;
  name?: string;
  exchangeShortName?: string;
  stockExchange?: string;
  currency?: string;
}

interface PortfolioHoldingPayload {
  symbol: string;
  instrument_type: string;
  exchange: string;
  name: string;
  quantity: string;
  average_cost: string;
  currency: string;
  account_name?: string;
  notes?: string;
  first_purchased_at: string;
}

interface AddPortfolioHoldingDialogProps {
  open?: boolean;
  onClose?: () => void;
  onAdd: (payload: PortfolioHoldingPayload) => void;
}

/**
 * Two-page dialog for adding portfolio holdings:
 * Page 1: Search for stocks by keyword
 * Page 2: Fill in quantity, average cost, account name, and notes
 */
function AddPortfolioHoldingDialog({
  open = false,
  onClose,
  onAdd,
}: AddPortfolioHoldingDialogProps) {
  const [page, setPage] = useState<1 | 2>(1); // 1 = search, 2 = details
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<StockResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [selectedStock, setSelectedStock] = useState<StockResult | null>(null);

  // Form fields for page 2
  const [quantity, setQuantity] = useState('');
  const [averageCost, setAverageCost] = useState('');
  const [accountName, setAccountName] = useState('');
  const [notes, setNotes] = useState('');

  // Debounced search
  useEffect(() => {
    if (!open || page !== 1) {
      setSearchResults([]);
      return;
    }

    const query = searchQuery.trim();
    if (!query || query.length < 1) {
      setSearchResults([]);
      return;
    }

    const timeoutId = setTimeout(async () => {
      setSearchLoading(true);
      try {
        // Use maximum limit of 100 to show more search results
        const result = await searchStocks(query, 100);
        setSearchResults((result.results || []) as StockResult[]);
      } catch (error) {
        console.error('Search failed:', error);
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 300); // 300ms debounce

    return () => clearTimeout(timeoutId);
  }, [searchQuery, open, page]);

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setPage(1);
      setSearchQuery('');
      setSearchResults([]);
      setSelectedStock(null);
      setQuantity('');
      setAverageCost('');
      setAccountName('');
      setNotes('');
    }
  }, [open]);

  const handleStockSelect = (stock: StockResult) => {
    setSelectedStock(stock);
    setPage(2);
  };

  const handleBack = () => {
    setPage(1);
    setSelectedStock(null);
    setQuantity('');
    setAverageCost('');
    setAccountName('');
    setNotes('');
  };

  const handleAdd = () => {
    if (!selectedStock) return;

    // Validate required fields
    if (!quantity.trim()) {
      alert('Please enter quantity.');
      return;
    }
    if (!averageCost.trim()) {
      alert('Please enter average cost.');
      return;
    }

    const quantityNum = parseFloat(quantity);
    const averageCostNum = parseFloat(averageCost);

    if (isNaN(quantityNum) || quantityNum <= 0) {
      alert('Please enter a valid quantity greater than 0.');
      return;
    }

    if (isNaN(averageCostNum) || averageCostNum <= 0) {
      alert('Please enter a valid average cost greater than 0.');
      return;
    }

    // Build the payload according to API specification
    const payload = {
      symbol: selectedStock.symbol,
      instrument_type: 'stock',
      exchange: selectedStock.exchangeShortName || '',
      name: selectedStock.name || '',
      quantity: String(quantityNum),
      average_cost: String(averageCostNum),
      currency: selectedStock.currency || 'USD',
      account_name: accountName.trim() || undefined,
      notes: notes.trim() || undefined,
      first_purchased_at: new Date().toISOString(),
    };

    onAdd(payload);
  };

  const exchange = selectedStock?.exchangeShortName || selectedStock?.stockExchange || '';

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onClose?.()}>
      <DialogContent className="sm:max-w-md border max-sm:min-h-[60dvh]" style={{ backgroundColor: 'var(--color-bg-elevated)', borderColor: 'var(--color-border-elevated)' }}>
        {page === 1 ? (
          <>
            <DialogHeader className="text-left">
              <DialogTitle className="title-font" style={{ color: 'var(--color-text-primary)' }}>
                Add Portfolio Holding
              </DialogTitle>
            </DialogHeader>
            <div className="pt-1">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4" style={{ color: 'var(--color-text-secondary)' }} />
                <Input
                  placeholder="Search by symbol or company name..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-10 border"
                  style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-default)', color: 'var(--color-text-primary)' }}
                  autoFocus
                />
              </div>
              <ScrollArea className="mt-3 max-h-[50dvh] sm:max-h-[400px]">
                {searchLoading ? (
                  <div className="py-8 text-center text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                    Searching...
                  </div>
                ) : searchResults.length === 0 && searchQuery.trim().length >= 1 ? (
                  <div className="py-8 text-center text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                    No results found
                  </div>
                ) : searchResults.length === 0 ? (
                  <div className="py-8 text-center text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                    Type to search for stocks...
                  </div>
                ) : (
                  <div className="divide-y" style={{ borderColor: 'var(--color-border-muted)' }}>
                    {searchResults.map((stock, index) => (
                      <button
                        key={`${stock.symbol}-${index}`}
                        type="button"
                        onClick={() => handleStockSelect(stock)}
                        className="w-full text-left px-2 py-2.5 transition-colors flex items-center gap-3"
                        style={{ color: 'var(--color-text-primary)' }}
                        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--color-bg-hover)'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                      >
                        <span
                          className="shrink-0 w-12 text-center text-xs font-bold py-1 rounded"
                          style={{ backgroundColor: 'var(--color-bg-surface, var(--color-bg-hover))', color: 'var(--color-text-primary)' }}
                        >
                          {stock.symbol}
                        </span>
                        <span className="text-sm truncate" style={{ color: 'var(--color-text-secondary)' }}>
                          {stock.name}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </div>
          </>
        ) : (
          <>
            <DialogHeader className="text-left">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleBack}
                  className="p-1.5 -ml-1 rounded-lg transition-colors"
                  style={{ color: 'var(--color-text-secondary)' }}
                  onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--color-bg-hover)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                  aria-label="Back"
                >
                  <ArrowLeft className="h-4 w-4" />
                </button>
                <DialogTitle className="title-font" style={{ color: 'var(--color-text-primary)' }}>
                  Add to Portfolio
                </DialogTitle>
              </div>
            </DialogHeader>
            {selectedStock && (
              <div className="space-y-5">
                {/* Stock info card */}
                <div
                  className="rounded-xl p-4 border"
                  style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-muted)' }}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-bold" style={{ color: 'var(--color-text-primary)' }}>
                      {selectedStock.symbol}
                    </span>
                    {exchange && (
                      <span
                        className="text-[10px] font-medium px-1.5 py-0.5 rounded"
                        style={{ backgroundColor: 'var(--color-bg-surface, var(--color-bg-hover))', color: 'var(--color-text-secondary)' }}
                      >
                        {exchange}
                      </span>
                    )}
                  </div>
                  <div className="text-xs mt-1 truncate" style={{ color: 'var(--color-text-secondary)' }}>
                    {selectedStock.name}
                  </div>
                </div>

                {/* Quantity + Average Cost — side by side */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--color-text-secondary)' }}>
                      Quantity <span style={{ color: 'var(--color-loss)' }}>*</span>
                    </label>
                    <Input
                      type="number"
                      min="0"
                      step="any"
                      placeholder="e.g. 10.5"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                      className="border"
                      style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-default)', color: 'var(--color-text-primary)' }}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--color-text-secondary)' }}>
                      Avg Cost <span style={{ color: 'var(--color-loss)' }}>*</span>
                    </label>
                    <Input
                      type="number"
                      min="0"
                      step="any"
                      placeholder="e.g. 175.50"
                      value={averageCost}
                      onChange={(e) => setAverageCost(e.target.value)}
                      className="border"
                      style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-default)', color: 'var(--color-text-primary)' }}
                    />
                  </div>
                </div>

                {/* Account Name */}
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--color-text-secondary)' }}>
                    Account Name
                  </label>
                  <Input
                    placeholder="e.g. Robinhood"
                    value={accountName}
                    onChange={(e) => setAccountName(e.target.value)}
                    className="border"
                    style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-default)', color: 'var(--color-text-primary)' }}
                  />
                </div>

                {/* Notes Input */}
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--color-text-secondary)' }}>
                    Notes
                  </label>
                  <Input
                    placeholder="Optional"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    className="border"
                    style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-default)', color: 'var(--color-text-primary)' }}
                  />
                </div>

                {/* Add Button */}
                <button
                  type="button"
                  onClick={handleAdd}
                  className="w-full px-4 py-3 rounded-xl text-sm font-semibold hover:opacity-90 transition-opacity"
                  style={{ backgroundColor: 'var(--color-accent-primary)', color: 'var(--color-text-on-accent)' }}
                >
                  Add to Portfolio
                </button>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default AddPortfolioHoldingDialog;
