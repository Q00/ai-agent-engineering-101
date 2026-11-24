export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('.') && specifier.endsWith('.js')) {
    try { return await nextResolve(specifier.slice(0, -3) + '.ts', context); } catch (e) { if (e.code !== 'ERR_MODULE_NOT_FOUND') throw e; }
  }
  return nextResolve(specifier, context);
}










