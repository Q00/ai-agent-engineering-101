import { DurableObject } from 'cloudflare:workers';
import handler, {ShopCore} from './shop_core.js';
export default handler;
export class Shop extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.shop = new ShopCore(ctx, env); }
  fetch(request) { return this.shop.fetch(request); }
}
