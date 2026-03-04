import type { TimeRange } from "../hooks/useStats";
import { useLatestAttestation, useVerifyChain, useAttestationOrgs } from "../hooks/useAttestations";
import { CardShell } from "../components/common/CardShell";
import { InfoTip } from "../components/common/InfoTip";
import { LabeledValue } from "../components/common/LabeledValue";
import { truncateHash } from "../utils/format";

interface Props {
  timeRange: TimeRange;
}

export function Attestations({ timeRange: _timeRange }: Props) {
  const { data: orgsData, isLoading: orgsLoading } = useAttestationOrgs();
  const orgHashes = orgsData?.org_hashes ?? [];
  const orgHash = orgHashes[0] ?? null;

  return (
    <div className="flex flex-col gap-6 max-w-[1600px] mx-auto">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Attestations</h1>
        <InfoTip text="Cryptographic commitments to your AI operations data, recorded on-chain. Each attestation covers a weekly period and proves the underlying metrics haven't been tampered with." />
        {orgHashes.length > 1 && (
          <span className="text-[10px] text-gray-500">
            {orgHashes.length} orgs
          </span>
        )}
      </div>

      {orgsLoading ? (
        <div className="text-xs text-gray-500">Loading...</div>
      ) : !orgHash ? (
        <div className="flex flex-col items-center justify-center py-16 text-gray-500">
          <svg className="w-10 h-10 mb-2 text-gray-700" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 1L3 4v4.5c0 3.5 2.5 5.5 5 6.5 2.5-1 5-3 5-6.5V4L8 1z" />
            <path d="M6 8h4M8 6v4" />
          </svg>
          <p className="text-xs">Waiting for first attestation</p>
          <p className="text-[10px] text-gray-600 mt-1">
            The scheduler will generate attestations automatically once LLM events are captured
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <LatestAttestationCard orgHash={orgHash} />
          <ChainIntegrityCard orgHash={orgHash} />
        </div>
      )}
    </div>
  );
}

function LatestAttestationCard({ orgHash }: { orgHash: string }) {
  const { data, isLoading, error } = useLatestAttestation(orgHash);

  const is404 = error?.message?.includes("404");

  return (
    <CardShell title="Latest Attestation" loading={isLoading} error={is404 ? null : (error ?? null)} skeletonHeight="h-48">
      {is404 && (
        <div className="flex flex-col items-center justify-center py-8 text-gray-500">
          <p className="text-xs">No attestations submitted yet</p>
        </div>
      )}
      {data && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <LabeledValue label="Nonce" value={`#${data.nonce}`} tip="Sequential record number for this org's attestation chain" />
            <LabeledValue label="Timestamp" value={new Date(data.timestamp).toLocaleString()} />
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <LabeledValue label="Period Start" value={new Date(data.period_start).toLocaleDateString()} tip="Start of the time window this attestation covers" />
            <LabeledValue label="Period End" value={new Date(data.period_end).toLocaleDateString()} tip="End of the time window this attestation covers" />
          </div>
          <div className="border-t border-gray-800 pt-3 flex flex-col gap-2">
            <HashField label="Metrics Hash" hash={data.metrics_hash} tip="SHA-256 fingerprint of aggregated spend, waste, and usage metrics for this period" />
            <HashField label="Benchmark Hash" hash={data.benchmark_hash} tip="Fingerprint of the model fitness matrix snapshot" />
            <HashField label="Merkle Root" hash={data.merkle_root} tip="Root hash of a tree where each leaf is one trace's evaluation — allows proving any single trace was included without revealing others" />
            <HashField label="Prev Hash" hash={data.prev_hash} tip="Hash of the previous attestation, forming an immutable chain" />
          </div>
        </div>
      )}
    </CardShell>
  );
}

function ChainIntegrityCard({ orgHash }: { orgHash: string }) {
  const { data, isLoading, error } = useVerifyChain(orgHash);

  return (
    <CardShell title={<span className="flex items-center gap-1.5">Chain Integrity <InfoTip text="Verifies that each attestation's prev_hash correctly references the one before it — an unbroken chain means no records were altered or removed" /></span>} loading={isLoading} error={error ?? null} skeletonHeight="h-48">
      {data && (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            {data.chain_valid ? (
              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-green-500/10 text-green-400 ring-1 ring-green-500/20">
                <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 8l3 3 7-7" />
                </svg>
                Valid
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-red-500/10 text-red-400 ring-1 ring-red-500/20">
                <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
                Broken
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <LabeledValue label="Latest Nonce" value={`#${data.latest_nonce}`} />
            <LabeledValue label="Records Checked" value={String(data.records_checked)} />
            {data.first_broken_nonce !== null && (
              <LabeledValue label="First Broken" value={`#${data.first_broken_nonce}`} className="text-red-400" />
            )}
          </div>
        </div>
      )}
    </CardShell>
  );
}

function HashField({ label, hash, tip }: { label: string; hash: string; tip?: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-gray-500 uppercase w-28 shrink-0 flex items-center gap-1">
        {label}
        <InfoTip text={tip} />
      </span>
      <span className="text-xs font-mono text-gray-400" title={hash}>
        {truncateHash(hash)}
      </span>
    </div>
  );
}
