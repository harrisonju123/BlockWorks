import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getLatestAttestation,
  verifyChain,
  submitAttestation,
  getAttestationOrgs,
} from "../api/client";

export function useLatestAttestation(orgIdHash: string) {
  return useQuery({
    queryKey: ["attestation-latest", orgIdHash],
    queryFn: () => getLatestAttestation(orgIdHash),
    enabled: !!orgIdHash,
    retry: false,
    staleTime: 60_000,
  });
}

export function useVerifyChain(orgIdHash: string) {
  return useQuery({
    queryKey: ["attestation-verify", orgIdHash],
    queryFn: () => verifyChain(orgIdHash),
    enabled: !!orgIdHash,
    staleTime: 60_000,
  });
}

export function useAttestationOrgs() {
  return useQuery({
    queryKey: ["attestation-orgs"],
    queryFn: () => getAttestationOrgs(),
    staleTime: 30_000,
  });
}

export function useSubmitAttestation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => submitAttestation(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["attestation-latest"] });
      qc.invalidateQueries({ queryKey: ["attestation-verify"] });
    },
  });
}
