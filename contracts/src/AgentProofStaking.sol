// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentProof Validator Staking
/// @author AgentProof
/// @notice Manages validator stake deposits, withdrawals with cooldown,
///         and slashing for dishonest behavior.
/// @dev Designed for Base L2. Minimal implementation — stake ETH, unstake
///      after cooldown, slash by owner/governance.
contract AgentProofStaking {

    // ---------------------------------------------------------------
    //  Types
    // ---------------------------------------------------------------

    struct ValidatorStake {
        uint256 amount;
        uint40  registeredAt;
        uint40  unstakeRequestedAt;  // 0 = no pending unstake
        uint256 unstakeAmount;       // amount requested for unstake
        bool    isValidator;
    }

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    mapping(address => ValidatorStake) public stakes;

    address public owner;

    /// @dev Minimum stake required to be a validator (in wei)
    uint256 public minStake;

    /// @dev Cooldown period after unstake request before withdrawal (seconds)
    uint256 public cooldownPeriod;

    // ---------------------------------------------------------------
    //  Events
    // ---------------------------------------------------------------

    event Staked(address indexed validator, uint256 amount, uint256 totalStake);
    event UnstakeRequested(address indexed validator, uint256 amount, uint40 availableAt);
    event Unstaked(address indexed validator, uint256 amount, uint256 remainingStake);
    event Slashed(address indexed validator, uint256 amount, string reason);
    event MinStakeUpdated(uint256 oldMinStake, uint256 newMinStake);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error Unauthorized();
    error InsufficientStake(uint256 required, uint256 provided);
    error NotValidator();
    error CooldownNotElapsed(uint40 availableAt);
    error NoPendingUnstake();
    error InsufficientBalance(uint256 requested, uint256 available);
    error ZeroAddress();
    error ZeroAmount();
    error TransferFailed();

    // ---------------------------------------------------------------
    //  Modifiers
    // ---------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    // ---------------------------------------------------------------
    //  Constructor
    // ---------------------------------------------------------------

    /// @param initialOwner The governance address
    /// @param _minStake Minimum stake in wei (e.g. 0.1 ether)
    /// @param _cooldownPeriod Seconds before unstaked funds can be withdrawn
    constructor(address initialOwner, uint256 _minStake, uint256 _cooldownPeriod) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        minStake = _minStake;
        cooldownPeriod = _cooldownPeriod;
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    /// @notice Transfer contract ownership.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Update the minimum stake requirement.
    function setMinStake(uint256 _minStake) external onlyOwner {
        emit MinStakeUpdated(minStake, _minStake);
        minStake = _minStake;
    }

    // ---------------------------------------------------------------
    //  Staking
    // ---------------------------------------------------------------

    /// @notice Deposit ETH as validator stake. Becomes a validator if
    ///         total stake meets the minimum.
    function stake() external payable {
        if (msg.value == 0) revert ZeroAmount();

        ValidatorStake storage vs = stakes[msg.sender];
        vs.amount += msg.value;

        if (vs.amount >= minStake && !vs.isValidator) {
            vs.isValidator = true;
            vs.registeredAt = uint40(block.timestamp);
        }

        emit Staked(msg.sender, msg.value, vs.amount);
    }

    /// @notice Request unstaking. Starts the cooldown timer.
    ///         The actual withdrawal happens via `withdraw()` after cooldown.
    /// @param amount The amount of stake to unstake
    function requestUnstake(uint256 amount) external {
        if (amount == 0) revert ZeroAmount();

        ValidatorStake storage vs = stakes[msg.sender];
        if (!vs.isValidator) revert NotValidator();
        if (amount > vs.amount) {
            revert InsufficientBalance(amount, vs.amount);
        }

        vs.unstakeRequestedAt = uint40(block.timestamp);
        vs.unstakeAmount = amount;

        uint40 availableAt = uint40(block.timestamp + cooldownPeriod);
        emit UnstakeRequested(msg.sender, amount, availableAt);
    }

    /// @notice Withdraw previously unstaked funds after cooldown.
    function withdraw() external {
        ValidatorStake storage vs = stakes[msg.sender];

        if (vs.unstakeRequestedAt == 0) revert NoPendingUnstake();

        uint40 availableAt = vs.unstakeRequestedAt + uint40(cooldownPeriod);
        if (block.timestamp < availableAt) {
            revert CooldownNotElapsed(availableAt);
        }

        uint256 amount = vs.unstakeAmount;
        vs.amount -= amount;
        vs.unstakeRequestedAt = 0;
        vs.unstakeAmount = 0;

        // Lose validator status if stake falls below minimum
        if (vs.amount < minStake) {
            vs.isValidator = false;
        }

        emit Unstaked(msg.sender, amount, vs.amount);

        (bool success,) = msg.sender.call{value: amount}("");
        if (!success) revert TransferFailed();
    }

    // ---------------------------------------------------------------
    //  Slashing
    // ---------------------------------------------------------------

    /// @notice Slash a validator's stake. Only callable by owner/governance.
    /// @param validator The validator to slash
    /// @param amount The amount to slash (capped at current stake)
    /// @param reason Human-readable reason for the slash
    function slash(
        address validator,
        uint256 amount,
        string calldata reason
    ) external onlyOwner {
        if (amount == 0) revert ZeroAmount();

        ValidatorStake storage vs = stakes[validator];
        if (!vs.isValidator) revert NotValidator();

        // Cap slash at available stake
        uint256 actualSlash = amount > vs.amount ? vs.amount : amount;
        vs.amount -= actualSlash;

        // Lose validator status if stake falls below minimum
        if (vs.amount < minStake) {
            vs.isValidator = false;
        }

        emit Slashed(validator, actualSlash, reason);
    }

    // ---------------------------------------------------------------
    //  View
    // ---------------------------------------------------------------

    /// @notice Get a validator's current stake amount.
    function getStake(address validator) external view returns (uint256) {
        return stakes[validator].amount;
    }

    /// @notice Check if an address is a registered validator.
    function isValidator(address validator) external view returns (bool) {
        return stakes[validator].isValidator;
    }
}
