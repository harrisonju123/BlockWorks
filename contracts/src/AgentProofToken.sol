// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentProof Token (ERC-20)
/// @author AgentProof
/// @notice Utility token for staking, governance, payment, and rewards.
///         Fixed initial supply of 1B tokens. Deflationary via burn mechanics.
/// @dev Minimal ERC-20 implementation without OpenZeppelin. 18 decimals.
contract AgentProofToken {

    // ---------------------------------------------------------------
    //  State
    // ---------------------------------------------------------------

    string public constant name = "AgentProof";
    string public constant symbol = "APR";
    uint8  public constant decimals = 18;

    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    address public owner;

    // ---------------------------------------------------------------
    //  Events (ERC-20 standard)
    // ---------------------------------------------------------------

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // ---------------------------------------------------------------
    //  Errors
    // ---------------------------------------------------------------

    error Unauthorized();
    error InsufficientBalance();
    error InsufficientAllowance();
    error ZeroAddress();

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

    /// @param initialOwner Receives ownership and the initial 1B token supply.
    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();

        owner = initialOwner;

        // 1 billion tokens with 18 decimals
        uint256 initialSupply = 1_000_000_000 * 10 ** 18;
        totalSupply = initialSupply;
        balanceOf[initialOwner] = initialSupply;

        emit Transfer(address(0), initialOwner, initialSupply);
    }

    // ---------------------------------------------------------------
    //  ERC-20: Transfer
    // ---------------------------------------------------------------

    /// @notice Transfer tokens to a recipient.
    /// @param to    The recipient address.
    /// @param value The amount of tokens to transfer.
    /// @return True on success.
    function transfer(address to, uint256 value) external returns (bool) {
        if (to == address(0)) revert ZeroAddress();
        if (balanceOf[msg.sender] < value) revert InsufficientBalance();

        unchecked {
            balanceOf[msg.sender] -= value;
        }
        balanceOf[to] += value;

        emit Transfer(msg.sender, to, value);
        return true;
    }

    // ---------------------------------------------------------------
    //  ERC-20: Approve / TransferFrom
    // ---------------------------------------------------------------

    /// @notice Approve a spender to transfer tokens on your behalf.
    /// @param spender The address authorized to spend.
    /// @param value   The maximum amount they can spend.
    /// @return True on success.
    function approve(address spender, uint256 value) external returns (bool) {
        if (spender == address(0)) revert ZeroAddress();

        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    /// @notice Transfer tokens on behalf of the owner, consuming allowance.
    /// @param from  The token owner.
    /// @param to    The recipient.
    /// @param value The amount to transfer.
    /// @return True on success.
    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        if (to == address(0)) revert ZeroAddress();
        if (balanceOf[from] < value) revert InsufficientBalance();

        uint256 currentAllowance = allowance[from][msg.sender];
        if (currentAllowance < value) revert InsufficientAllowance();

        unchecked {
            allowance[from][msg.sender] = currentAllowance - value;
            balanceOf[from] -= value;
        }
        balanceOf[to] += value;

        emit Transfer(from, to, value);
        return true;
    }

    // ---------------------------------------------------------------
    //  Mint / Burn
    // ---------------------------------------------------------------

    /// @notice Mint new tokens. Only callable by owner (for initial
    ///         distribution and reward programs).
    /// @param to     The recipient of the minted tokens.
    /// @param amount The number of tokens to mint.
    function mint(address to, uint256 amount) external onlyOwner {
        if (to == address(0)) revert ZeroAddress();

        totalSupply += amount;
        balanceOf[to] += amount;

        emit Transfer(address(0), to, amount);
    }

    /// @notice Burn tokens from the caller's balance. Used by the
    ///         deflationary mechanism (percentage of premium payments burned).
    /// @param amount The number of tokens to burn.
    function burn(uint256 amount) external {
        if (balanceOf[msg.sender] < amount) revert InsufficientBalance();

        unchecked {
            balanceOf[msg.sender] -= amount;
        }
        totalSupply -= amount;

        emit Transfer(msg.sender, address(0), amount);
    }

    // ---------------------------------------------------------------
    //  Admin
    // ---------------------------------------------------------------

    /// @notice Transfer contract ownership.
    /// @param newOwner The address of the new owner.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
