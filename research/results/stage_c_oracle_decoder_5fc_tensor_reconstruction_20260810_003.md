# EXP-016C Tensor-Space Decoder Reconstruction

Status: **completed**

## Contract

For each target and fold, decoder weights and free train-pair z values were
trained only on the 128 decoder-train DeltaE rows. Held-out rows never entered
tensor-space training. The fixed loss was normalized MSE plus
`0.1 * (1 - cosine)`. Linear decoders were initialized from the train-fold
rank-128 SVD basis; MLP decoders used the repository's no-bias
`128 -> 512 -> 4*4096` injector parameterization. Zero z therefore produces
exactly zero DeltaE.

## Results

| Target | Fold | Decoder | Best epoch | Relative Frobenius error | Cosine | Status |
|---|---:|---|---:|---:|---:|---|
| u112 | 0 | linear | 48 | 0.00000892 | 1.00000000 | completed |
| u112 | 0 | MLP | 592 | 0.00058962 | 0.99999988 | completed |
| u112 | 1 | linear | 16 | 0.00001566 | 1.00000000 | completed |
| u112 | 1 | MLP | 560 | 0.00187839 | 0.99999821 | completed |
| u112 | 2 | linear | 48 | 0.00001220 | 1.00000000 | completed |
| u112 | 2 | MLP | 496 | 0.00072462 | 0.99999964 | completed |
| u128 | 0 | linear | 16 | 0.00001558 | 1.00000000 | completed |
| u128 | 0 | MLP | 496 | 0.00081342 | 0.99999893 | completed |
| u128 | 1 | linear | 64 | 0.00000830 | 1.00000000 | completed |
| u128 | 1 | MLP | 560 | 0.00149198 | 0.99999881 | completed |
| u128 | 2 | linear | 48 | 0.00001198 | 1.00000000 | completed |
| u128 | 2 | MLP | 448 | 0.00137751 | 0.99999845 | completed |

Every tensor-space run reached its documented prospective plateau or
numerical-floor criterion before its 2048-epoch cap. The final saved decoder
for each run is the best checkpoint, not merely the last epoch.

## Conclusion

Both decoder architectures reconstruct train-fold direct DeltaE nearly
exactly. The linear decoder is consistently strongest in tensor space, while
the MLP remains below 0.19% relative Frobenius error in every fold. Therefore
the final scientific-gate failure cannot be attributed to failure to fit the
decoder-training tensors. The unresolved issue is held-out inversion
convergence/generalization through frozen Qwen.

No Qwen forward pass was used during tensor-space training.
